"""Persistent cards and read-only discovery; never executes real launchers."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ringfour_acceptance.model import Store
from ringfour_acceptance.virtual import inventory


def components(state="running"):
    return {
        name: {"state": state, "container": "fixture-" + name}
        for name in ("runner", "daemon", "sim")
    }


class StackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state")
        self.observed = {
            "root": str(self.root),
            "available": True,
            "observed_at": "2026-11-26T20:00:00Z",
            "stacks": [
                {"topology": "singleton", "index": 1, "components": components()},
                {"topology": "numbered", "index": 2, "components": components()},
            ],
        }
        registration = self.store.register(
            self.store.pairing(), "fixture", {}, self.observed
        )
        self.worker = self.store.authenticate(registration["token"])

    def test_adds_next_stack_without_launch_and_persists_all_cards(self):
        self.assertEqual(len(self.store.stacks), 2)
        third = self.store.add_stack(self.worker["id"])
        fourth = self.store.add_stack(self.worker["id"])
        self.assertEqual([third["index"], fourth["index"]], [3, 4])
        self.assertEqual(set(third["components"]), {"runner", "daemon", "sim"})
        self.assertFalse(self.store.jobs)
        reloaded = Store(self.store.root)
        self.assertEqual(len(reloaded.stacks), 4)
        self.assertEqual(
            json.loads((self.store.root / "stacks.json").read_text())[third["id"]][
                "index"
            ],
            3,
        )

    def test_card_action_is_bound_to_one_exact_stack(self):
        third = self.store.add_stack(self.worker["id"])
        self.store.configure_stack(third["id"], self.worker["id"], True)
        job = self.store.stack_action(third["id"], "start")
        self.assertEqual(job["stack_id"], third["id"])
        self.assertEqual(
            job["launcher"]["spec"],
            {
                "action": "start",
                "topology": "numbered",
                "start_index": 3,
                "count": 1,
                "allow_dirty": True,
            },
        )
        with self.assertRaisesRegex(ValueError, "current operation"):
            self.store.configure_stack(third["id"], self.worker["id"], False)
        with self.assertRaises(ValueError):
            self.store.stack_action(third["id"], "arbitrary-command")
        self.assertEqual(len(self.store.jobs), 1)

    def test_fresh_inventory_clears_absent_containers_and_reconnects_same_cards(self):
        self.worker["launchers"] = self.observed | {"stacks": []}
        self.store.sync_stacks(self.worker)
        numbered = next(
            s for s in self.store.stacks.values() if s["topology"] == "numbered"
        )
        self.assertTrue(
            all(c["state"] == "not_started" for c in numbered["components"].values())
        )
        old_ids = set(self.store.stacks)
        reloaded = Store(self.store.root)
        new = reloaded.register(reloaded.pairing(), "reconnected", {}, self.observed)
        self.assertEqual(set(reloaded.stacks), old_ids)
        self.assertTrue(
            all(s["worker_id"] == new["worker_id"] for s in reloaded.stacks.values())
        )

    def test_unavailable_inventory_does_not_claim_containers_are_stopped(self):
        self.worker["launchers"] = self.observed | {
            "inventory_error": "Docker unavailable",
            "stacks": [],
        }
        self.store.sync_stacks(self.worker)
        self.assertTrue(
            all(
                c["state"] == "running"
                for stack in self.store.stacks.values()
                for c in stack["components"].values()
            )
        )

    def test_delete_keeps_card_until_confirmed_and_ignores_stale_inventory(self):
        stack = self.store.add_stack(self.worker["id"])
        job = self.store.stack_action(stack["id"], "delete")
        self.assertIn(stack["id"], self.store.stacks)
        self.store.claim(self.worker)
        self.store.update(self.worker, job["id"], {"status": "failed"})
        self.assertIn(stack["id"], self.store.stacks)
        job = self.store.stack_action(stack["id"], "delete")
        self.store.claim(self.worker)
        self.store.update(self.worker, job["id"], {"status": "passed"})
        self.assertEqual(job["status"], "failed")
        self.assertIn(stack["id"], self.store.stacks)
        job = self.store.stack_action(stack["id"], "delete")
        self.store.claim(self.worker)
        self.store.update(
            self.worker,
            job["id"],
            {
                "status": "passed",
                "progress": {
                    "virtual-operation": {"passed": True, "stage": "completed"}
                },
            },
        )
        self.assertNotIn(stack["id"], self.store.stacks)
        self.worker["launchers"] = self.observed | {
            "stacks": self.observed["stacks"]
            + [{"topology": "numbered", "index": 3, "components": components()}]
        }
        self.store.sync_stacks(self.worker)
        self.assertNotIn(stack["id"], self.store.stacks)
        reloaded = Store(self.store.root)
        self.assertIn(stack["id"], reloaded.deleted_stacks)
        replacement = self.store.add_stack(self.worker["id"])
        self.assertEqual(replacement["index"], 3)
        self.assertNotIn(replacement["id"], self.store.deleted_stacks)

    def test_discovery_uses_exact_service_labels_and_shows_partial_stacks(self):
        for name in ("up.sh", "upn.sh", "down.sh"):
            (self.root / name).touch()

        def row(project, service):
            return {
                "Labels": f"com.docker.compose.project={project},com.docker.compose.service={service}",
                "Names": project + "-" + service,
                "State": "running",
                "Status": "Up",
            }

        rows = [
            row("ringfour-virtual", "agent"),
            row("ringfour-virtual", "daemon"),
            row("ringfour-sim-1", "simulator"),
            row("ringfour-virtual-2", "agent"),
            row("ringfour-virtual-2", "daemon"),
            row("ringfour-sim-2", "simulator"),
            row("ringfour-sim-3", "simulator"),
            row("unrelated", "daemon"),
        ]
        with patch("ringfour_acceptance.virtual.docker_rows", return_value=rows):
            result = inventory(self.root)
        self.assertEqual(len(result["stacks"]), 3)
        singleton = next(s for s in result["stacks"] if s["topology"] == "singleton")
        self.assertEqual(singleton["components"]["sim"]["state"], "external")
        second = next(s for s in result["stacks"] if s["index"] == 2)
        self.assertTrue(
            all(c["state"] == "running" for c in second["components"].values())
        )
        third = next(s for s in result["stacks"] if s["index"] == 3)
        self.assertEqual(third["components"]["sim"]["state"], "running")
        self.assertEqual(third["components"]["daemon"]["state"], "not_started")


class IdentityTests(unittest.TestCase):
    def test_device_identity_is_read_from_status_detail_not_app_output(self):
        from ringfour_acceptance.virtual import DeviceIdentityParser

        robot_id = "f0bc5e22-6045-4fb5-971c-165d7d538a86"
        parser = DeviceIdentityParser()
        parser.feed(
            f'<title>Ringfour device</title><div class="detail">Robot ID: {robot_id}</div>'
        )
        self.assertEqual(
            parser.identity(), {"robot_id": robot_id, "identity_state": "enrolled"}
        )
        parser = DeviceIdentityParser()
        parser.feed(
            f'<title>Ringfour device</title><div class="detail">ABCD EFGH</div><pre>Robot ID: {robot_id}</pre>'
        )
        self.assertEqual(
            parser.identity(), {"robot_id": None, "identity_state": "not_enrolled"}
        )
        parser = DeviceIdentityParser()
        parser.feed(
            f'<title>Other app</title><div class="detail">Robot ID: {robot_id}</div>'
        )
        self.assertEqual(parser.identity()["identity_state"], "unavailable")

    def test_current_robot_id_is_retained_as_last_known_then_cleared_on_unenroll(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            observed = {
                "root": directory,
                "available": True,
                "stacks": [
                    {
                        "topology": "numbered",
                        "index": 2,
                        "components": components(),
                        "ports": {"agent": 10111, "daemon": 8091, "status": 8093},
                        "robot_id": "real-robot",
                        "identity_state": "enrolled",
                    }
                ],
            }
            registration = store.register(store.pairing(), "fixture", {}, observed)
            worker = store.authenticate(registration["token"])
            stack = next(iter(store.stacks.values()))
            self.assertEqual(stack["robot_id"], "real-robot")
            self.assertEqual(stack["ports"]["status"], 8093)
            worker["launchers"] = observed | {"stacks": []}
            store.sync_stacks(worker)
            self.assertEqual(stack["robot_id"], "real-robot")
            self.assertEqual(stack["identity_state"], "unavailable")
            worker["launchers"] = observed | {
                "stacks": [
                    observed["stacks"][0]
                    | {"robot_id": None, "identity_state": "not_enrolled"}
                ]
            }
            store.sync_stacks(worker)
            self.assertIsNone(stack["robot_id"])

    def test_fallback_reads_the_exact_running_container_status_page(self):
        from types import SimpleNamespace

        from ringfour_acceptance.virtual import daemon_identity

        robot_id = "f0bc5e22-6045-4fb5-971c-165d7d538a86"
        component = {"state": "running", "container_id": "abcdef123456", "ports": ""}
        response = SimpleNamespace(
            returncode=0,
            stdout=f'<title>Ringfour device</title><div class="detail">Robot ID: {robot_id}</div>'.encode(),
        )
        with patch(
            "ringfour_acceptance.virtual.subprocess.run", return_value=response
        ) as run:
            self.assertEqual(daemon_identity(component)["robot_id"], robot_id)
        self.assertEqual(
            run.call_args.args[0][:4], ["docker", "exec", "abcdef123456", "python"]
        )
        with patch("ringfour_acceptance.virtual.subprocess.run") as run:
            self.assertEqual(
                daemon_identity(component | {"state": "exited"})["identity_state"],
                "unavailable",
            )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
