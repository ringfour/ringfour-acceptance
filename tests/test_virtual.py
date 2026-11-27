"""Launcher checks use only temporary scripts/fake Docker; never real stacks."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ringfour_acceptance.model import Store
from ringfour_acceptance.virtual import (
    check_ports,
    metadata,
    normalize,
    plan,
)
from ringfour_acceptance.virtual import (
    execute as run_virtual,
)
from ringfour_acceptance.worker import command
from ringfour_acceptance.worker import execute as run_worker


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("up.sh", "upn.sh", "down.sh"):
            (self.root / name).write_text("#!/bin/sh\nexit 0\n")
            (self.root / name).chmod(0o755)

    def test_numbered_plan_reuses_launcher_and_has_correct_isolation(self):
        selected = plan(
            self.root,
            {
                "action": "start",
                "topology": "numbered",
                "start_index": 2,
                "count": 2,
                "allow_dirty": True,
            },
        )
        self.assertEqual(
            selected["argv"],
            [
                str(self.root / "upn.sh"),
                "2",
                "--start-index",
                "2",
                "--no-build",
                "--force-recreate",
            ],
        )
        self.assertEqual(
            selected["instances"][0]["ports"],
            {"agent": 10111, "daemon": 8091, "status": 8093, "development": 8453},
        )
        self.assertEqual(selected["instances"][1]["ros_domain"], 189)
        self.assertEqual(
            selected["projects"],
            [
                "ringfour-virtual-2",
                "ringfour-sim-2",
                "ringfour-virtual-3",
                "ringfour-sim-3",
            ],
        )
        self.assertEqual(
            selected["environment"], {"RINGFOUR_ALLOW_DIRTY_APP_SOURCES": "true"}
        )

    def test_build_only_prepares_shared_images_and_keeps_dirty_opt_in(self):
        selected = plan(self.root, {"action": "build", "allow_dirty": True})
        self.assertEqual(selected["argv"], [str(self.root / "build.sh")])
        self.assertEqual(
            selected["environment"], {"RINGFOUR_ALLOW_DIRTY_APP_SOURCES": "true"}
        )
        self.assertTrue(selected["stack_operations"])
        self.assertNotIn("up.sh", selected["command"])

    def test_unknown_options_ranges_and_volume_deletion_are_rejected(self):
        for spec in (
            {"action": "shell"},
            {"topology": "other"},
            {"start_index": 40, "count": 2},
            {"count": True},
            {"count": 2},
            {"allow_dirty": "true"},
            {"extra_args": ["-v"]},
        ):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                normalize(spec)
        selected = plan(
            self.root, {"action": "stop", "topology": "numbered", "start_index": 2}
        )
        self.assertEqual(
            selected["argv"], [str(self.root / "down.sh"), "--instance", "2"]
        )
        self.assertNotIn("-v", selected["argv"])

    def test_singleton_port_config_and_plan_drift(self):
        (self.root / ".env").write_text(
            "RINGFOUR_STATUS_HOST_PORT=8084\nSECRET=must-not-appear\n"
        )
        selected = plan(self.root, {"action": "daemon"})
        self.assertEqual(selected["instances"][0]["ports"]["status"], 8084)
        self.assertEqual(
            selected["argv"],
            [
                str(self.root / "up.sh"),
                "-d",
                "--no-build",
                "--force-recreate",
                "--no-deps",
                "daemon",
            ],
        )
        self.assertEqual(
            selected["environment"]["COMPOSE_PROJECT_NAME"], "ringfour-virtual"
        )
        self.assertNotIn("must-not-appear", json.dumps(selected))
        (self.root / ".env").write_text("RINGFOUR_STATUS_HOST_PORT=8085\n")
        job = {"profile": "virtual", "launcher": selected}
        with self.assertRaisesRegex(ValueError, "plan changed"):
            command(
                self.root.parent / "platform", job, self.root / "evidence", self.root
            )

    def test_port_collision_is_specific_and_own_project_is_allowed(self):
        selected = plan(self.root, {"action": "start"})
        row = {
            "State": "running",
            "Names": "adminer",
            "Ports": "127.0.0.1:8083->8080/tcp",
            "Labels": "com.docker.compose.project=ringfour-server",
        }
        with self.assertRaisesRegex(RuntimeError, "8083.*adminer"):
            check_ports([row], selected)
        row["Labels"] = "com.docker.compose.project=ringfour-virtual"
        check_ports([row], selected)

    def test_launcher_job_shares_queue_and_requires_current_capability(self):
        store = Store(self.root / "state")
        old = store.register(store.pairing(), "old", {})
        with self.assertRaisesRegex(ValueError, "no virtual launchers"):
            store.virtual_plan(old["worker_id"], {"action": "start"})
        registered = store.register(store.pairing(), "new", {}, metadata(self.root))
        worker = store.authenticate(registered["token"])
        selected = store.virtual_plan(worker["id"], {"action": "start"})
        with self.assertRaisesRegex(ValueError, "plan changed"):
            store.enqueue_virtual(worker["id"], selected["spec"], "wrong-hash")
        job = store.enqueue_virtual(
            worker["id"], selected["spec"], selected["fingerprint"]
        )
        self.assertEqual(job["profile"], "virtual")
        self.assertEqual(store.claim(worker)["id"], job["id"])
        old_worker = store.authenticate(old["token"])
        store.enqueue("quick", old_worker["id"])
        self.assertIsNone(store.claim(old_worker))

    def test_delete_unused_stack_is_read_only_and_running_delete_preserves_guard(self):
        selected = plan(
            self.root, {"action": "delete", "topology": "numbered", "start_index": 3}
        )
        self.assertEqual(
            selected["argv"], [str(self.root / "down.sh"), "--instance", "3"]
        )
        self.assertNotIn("-v", selected["argv"])
        with (
            patch("ringfour_acceptance.virtual.docker_rows", return_value=[]),
            patch("ringfour_acceptance.virtual.subprocess.Popen") as launch,
        ):
            run_virtual(
                self.root,
                selected["spec"],
                self.root / "unused",
                selected["fingerprint"],
            )
            launch.assert_not_called()
        report = json.loads((self.root / "unused/virtual-operation.json").read_text())
        self.assertTrue(report["passed"])
        self.assertFalse(report["stack_operations"])
        (self.root / "down.sh").write_text(
            '#!/bin/sh\necho "Active App runs remain"\nexit 1\n'
        )
        row = {
            "Labels": "com.docker.compose.project=ringfour-virtual-3",
            "Names": "fixture-daemon",
            "State": "running",
        }
        with (
            patch("ringfour_acceptance.virtual.docker_rows", return_value=[row]),
            self.assertRaisesRegex(RuntimeError, "code 1"),
        ):
            run_virtual(
                self.root,
                selected["spec"],
                self.root / "blocked-delete",
                selected["fingerprint"],
            )
        report = json.loads(
            (self.root / "blocked-delete/virtual-operation.json").read_text()
        )
        self.assertFalse(report["passed"])

    def test_logs_are_separate_redacted_files_for_each_container(self):
        selected = plan(
            self.root, {"action": "logs", "topology": "numbered", "start_index": 2}
        )
        rows = [
            {
                "ID": service,
                "Names": "fixture-" + service,
                "State": "running",
                "Labels": "com.docker.compose.project=ringfour-virtual-2,com.docker.compose.service="
                + service,
            }
            for service in ("agent", "daemon")
        ]

        def response(args, **kwargs):
            if args[1] == "inspect":
                return SimpleNamespace(returncode=0, stdout='{"Error":""}', stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=args[-1] + " ready Bearer abc.def.ghi",
                stderr=args[-1] + " warning",
            )

        with (
            patch("ringfour_acceptance.virtual.docker_rows", return_value=rows),
            patch("ringfour_acceptance.virtual.subprocess.run", side_effect=response),
        ):
            run_virtual(
                self.root, selected["spec"], self.root / "logs", selected["fingerprint"]
            )
        report = json.loads((self.root / "logs/virtual-operation.json").read_text())
        self.assertTrue(report["passed"])
        first, second = report["containers"]
        self.assertEqual([first["service"], second["service"]], ["agent", "daemon"])
        self.assertNotEqual(first["logs"]["stdout"], second["logs"]["stdout"])
        self.assertEqual(
            (self.root / "logs" / first["logs"]["stderr"]).read_text(), "agent warning"
        )
        self.assertNotIn(
            "abc.def.ghi", (self.root / "logs" / first["logs"]["stdout"]).read_text()
        )
        self.assertIn(
            "[redacted]", (self.root / "logs" / first["logs"]["stdout"]).read_text()
        )
        self.assertNotIn(
            "agent warning", json.dumps(report)
        )  # References, not duplicated log bodies.

    def test_container_log_failure_keeps_its_stderr_and_fails_the_job(self):
        selected = plan(self.root, {"action": "logs"})
        row = {
            "ID": "agent",
            "Names": "fixture-agent",
            "Labels": "com.docker.compose.project=ringfour-virtual,com.docker.compose.service=agent",
        }

        def response(args, **kwargs):
            return (
                SimpleNamespace(returncode=0, stdout='{"Error":""}', stderr="")
                if args[1] == "inspect"
                else SimpleNamespace(
                    returncode=1, stdout="", stderr="container disappeared"
                )
            )

        with (
            patch("ringfour_acceptance.virtual.docker_rows", return_value=[row]),
            patch("ringfour_acceptance.virtual.subprocess.run", side_effect=response),
            self.assertRaisesRegex(RuntimeError, "fixture-agent"),
        ):
            run_virtual(
                self.root,
                selected["spec"],
                self.root / "failed-logs",
                selected["fingerprint"],
            )
        report = json.loads(
            (self.root / "failed-logs/virtual-operation.json").read_text()
        )
        self.assertFalse(report["passed"])
        file = report["containers"][0]["logs"]["stderr"]
        self.assertEqual(
            (self.root / "failed-logs" / file).read_text(), "container disappeared"
        )

    def test_real_stub_launcher_streams_and_preserves_script_refusal(self):
        script = self.root / "up.sh"
        script.write_text(
            '#!/bin/sh\necho fixture-launcher-only\necho "$COMPOSE_PROJECT_NAME"\nexit 0\n'
        )
        selected = plan(self.root, {"action": "start"})
        with patch("ringfour_acceptance.virtual.docker_rows", return_value=[]):
            run_virtual(
                self.root,
                selected["spec"],
                self.root / "evidence",
                selected["fingerprint"],
            )
        report = json.loads((self.root / "evidence/virtual-operation.json").read_text())
        self.assertTrue(report["passed"])
        self.assertTrue(
            report["stack_operations"]
        )  # Fixture script only, no real Docker calls.
        self.assertIn(
            "ringfour-virtual", (self.root / "evidence/launcher.log").read_text()
        )
        down = self.root / "down.sh"
        down.write_text(
            '#!/bin/sh\necho "Active App runs remain; refusing shutdown"\nexit 1\n'
        )
        selected = plan(self.root, {"action": "stop"})
        with (
            patch("ringfour_acceptance.virtual.docker_rows", return_value=[]),
            self.assertRaisesRegex(RuntimeError, "code 1"),
        ):
            run_virtual(
                self.root,
                selected["spec"],
                self.root / "refused",
                selected["fingerprint"],
            )
        self.assertFalse(
            json.loads((self.root / "refused/virtual-operation.json").read_text())[
                "passed"
            ]
        )


class WorkerProcessTests(unittest.TestCase):
    def test_real_http_worker_cancel_waits_for_temporary_launcher_cleanup(self):
        from ringfour_acceptance.server import Server

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            virtual = root / "virtual"
            virtual.mkdir()
            for name in ("up.sh", "upn.sh", "down.sh"):
                script = virtual / name
                script.write_text("""#!/usr/bin/env python3
import signal,time
from pathlib import Path
root=Path(__file__).parent
def stop(*_):
 (root/'cleanup-confirmed').touch()
 raise SystemExit(130)
signal.signal(signal.SIGINT,stop)
(root/'started').touch()
print('temporary launcher active',flush=True)
while True: time.sleep(.1)
""")
                script.chmod(0o755)
            fakebin = root / "bin"
            fakebin.mkdir()
            docker = fakebin / "docker"
            docker.write_text(
                "#!/bin/sh\n# Disposable test command, never invokes Docker.\nexit 0\n"
            )
            docker.chmod(0o755)
            server = Server(("127.0.0.1", 0), root / "state")
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            try:
                registration = server.store.register(
                    server.store.pairing(), "fixture", {}, metadata(virtual)
                )
                worker = server.store.authenticate(registration["token"])
                selected = server.store.virtual_plan(worker["id"], {"action": "start"})
                job = server.store.enqueue_virtual(
                    worker["id"], selected["spec"], selected["fingerprint"]
                )
                server.store.claim(worker)
                with patch.dict(
                    os.environ, {"PATH": str(fakebin) + os.pathsep + os.environ["PATH"]}
                ):
                    thread = threading.Thread(
                        target=run_worker,
                        args=(
                            f"http://127.0.0.1:{server.server_port}",
                            registration["token"],
                            root / "platform",
                            job,
                            threading.Event(),
                            virtual,
                        ),
                    )
                    thread.start()
                    deadline = time.monotonic() + 10
                    while (
                        not (virtual / "started").exists()
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.1)
                    self.assertTrue((virtual / "started").exists())
                    server.store.cancel(job["id"])
                    thread.join(15)
                    self.assertFalse(thread.is_alive())
                self.assertTrue((virtual / "cleanup-confirmed").exists())
                self.assertEqual(job["status"], "interrupted")
                self.assertFalse(job["progress"]["virtual-operation"]["passed"])
                self.assertTrue(job["progress"]["virtual-operation"]["interrupted"])
            finally:
                server.shutdown()
                server.server_close()
                serving.join()


if __name__ == "__main__":
    unittest.main()
