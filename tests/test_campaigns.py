import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from ringfour_acceptance.model import Store
from ringfour_acceptance.worker import command


def uid(n):
    return str(uuid.UUID(int=n))


def environment():
    return {
        "name": "Two real actors",
        "repository": "localhost:5000/ap",
        "roles": {
            name: {
                "runner_label": name,
                "user_id": uid(i),
                "org_id": uid(i + 10),
                "robot_id": uid(i + 20),
            }
            for i, name in enumerate(("A", "B"), 1)
        },
    }


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Store(self.folder.name)
        self.environment = self.store.save_environment(environment())
        self.workers = {}
        for name, role in self.environment["roles"].items():
            identity = {
                "user": {"id": role["user_id"]},
                "organizations": [
                    {"id": role["org_id"], "robots": [{"id": role["robot_id"]}]}
                ],
            }
            registration = self.store.register(
                self.store.pairing(),
                name,
                identity,
                build={"compliance": True, "fingerprint": "source-v1"},
            )
            self.workers[name] = self.store.authenticate(registration["token"])

    def case(self, campaign, name):
        return next(c for c in campaign["cases"] if c["id"] == name)

    def finish(self, worker, job, status="passed", data=None):
        self.store.update(
            worker,
            job["id"],
            {
                "status": status,
                "progress": {
                    "case-report": {
                        "status": status,
                        "data": data or {},
                        "error": "Deliberate fixture failure"
                        if status == "failed"
                        else None,
                    }
                },
            },
        )

    def drain(self, campaign, failure=None):
        visited = []
        for _ in range(200):
            job = None
            for worker in self.workers.values():
                job = self.store.claim(worker)
                if job:
                    break
            if not job:
                break
            visited.append(job["case_id"])
            actor = campaign["actors"].get(job["actor_role"], {}).get("data", {})
            self.finish(
                worker, job, "failed" if job["case_id"] == failure else "passed", actor
            )
        return visited

    def test_environment_rejects_duplicate_principals_orgs_robots_and_daemons(self):
        for key in ("user_id", "org_id", "robot_id", "stack_id"):
            draft = environment()
            if key == "stack_id":
                draft["roles"]["A"][key] = "same-stack"
            draft["roles"]["B"][key] = draft["roles"]["A"][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.save_environment(draft)
        for url in (
            "file:///etc/passwd",
            "http://name:secret@localhost:8080",
            "http://localhost:8080/?token=x",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.store.save_environment(environment() | {"api_url": url})

    def test_preflight_exact_users_and_persistent_editable_ids(self):
        campaign = self.store.create_campaign(self.environment["id"], "preflight")
        first = self.store.claim(self.workers["A"])
        self.assertEqual(first["case_id"], "preflight-A")
        self.assertIsNone(self.store.claim(self.workers["B"]))
        self.assertEqual(first["target"]["robot_id"], uid(21))
        self.finish(
            self.workers["A"], first, data={"robot_id": uid(21), "baseline": {}}
        )
        second = self.store.claim(self.workers["B"])
        self.assertEqual(second["case_id"], "preflight-B")
        self.finish(self.workers["B"], second)
        self.assertEqual(
            campaign["status"], "pending"
        )  # optional role evidence was not manufactured
        self.assertFalse(campaign["full_ap_acceptance"])
        persisted = Store(self.folder.name)
        self.assertEqual(
            persisted.environments[self.environment["id"]]["roles"],
            self.environment["roles"],
        )
        self.assertTrue(
            (persisted.root / "campaigns" / campaign["id"] / "report.md").exists()
        )

    def test_both_robot_preflights_must_pass_before_live_mutations(self):
        campaign = self.store.create_campaign(self.environment["id"], "live")
        self.finish(
            self.workers["A"],
            self.store.claim(self.workers["A"]),
            data={"baseline": {}},
        )
        self.finish(self.workers["B"], self.store.claim(self.workers["B"]), "failed")
        visited = self.drain(campaign)
        self.assertNotIn("prepare-A", visited)
        self.assertNotIn("prepare-B", visited)
        self.assertEqual(self.case(campaign, "prepare-A")["status"], "blocked")
        self.assertIn("cleanup-A", visited)
        self.assertEqual(campaign["status"], "failed")

    def test_partial_owned_ids_survive_failure_then_cleanup_runs_on_owner(self):
        campaign = self.store.create_campaign(self.environment["id"], "live")
        for name in ("A", "B"):
            self.finish(
                self.workers[name],
                self.store.claim(self.workers[name]),
                data={"baseline": {}},
            )
        job = self.store.claim(self.workers["A"])
        self.assertEqual(job["case_id"], "prepare-A")
        owned = {"owned_installations": [uid(100)], "baseline": {}}
        self.store.update(
            self.workers["A"], job["id"], {"progress": {"case-report": {"data": owned}}}
        )
        self.assertEqual(campaign["actors"]["A"]["data"], owned)
        # A final error without a report must not erase the already checkpointed IDs.
        self.store.update(
            self.workers["A"],
            job["id"],
            {"status": "failed", "reason": "registry fixture failure"},
        )
        visited = self.drain(campaign)
        cleanup = next(
            j for j in self.store.jobs.values() if j.get("case_id") == "cleanup-A"
        )
        self.assertEqual(cleanup["actors"]["A"]["data"], owned)
        self.assertEqual(cleanup["worker_id"], self.workers["A"]["id"])
        self.assertIn("cleanup-B", visited)
        self.assertFalse(campaign["automated_passed"])

    def test_cancellation_waits_for_inflight_report_then_always_cleans(self):
        campaign = self.store.create_campaign(self.environment["id"], "live")
        job = self.store.claim(self.workers["A"])
        self.store.cancel_campaign(campaign["id"])
        self.assertEqual(job["status"], "stopping")
        self.assertIsNone(self.store.claim(self.workers["B"]))
        self.finish(
            self.workers["A"], job, "interrupted", {"owned_installations": [uid(100)]}
        )
        visited = self.drain(campaign)
        self.assertEqual(visited, ["cleanup-A"])
        self.assertEqual(campaign["status"], "interrupted")

    def test_restart_never_replays_and_explicit_cleanup_retains_frozen_ids(self):
        campaign = self.store.create_campaign(self.environment["id"], "live")
        job = self.store.claim(self.workers["A"])
        self.store.update(
            self.workers["A"],
            job["id"],
            {
                "progress": {
                    "case-report": {"data": {"owned_installations": [uid(100)]}}
                }
            },
        )
        self.store = Store(self.folder.name)
        restored = self.store.campaigns[campaign["id"]]
        self.assertEqual(restored["status"], "interrupted")
        self.assertEqual(
            restored["actors"]["A"]["data"]["owned_installations"], [uid(100)]
        )
        registration = self.store.register(
            self.store.pairing(),
            "A",
            self.workers["A"]["identity"],
            build={"compliance": True, "fingerprint": "source-v2"},
        )
        worker = self.store.authenticate(registration["token"])
        self.assertIsNone(self.store.claim(worker))
        self.store.recover_campaign_cleanup(campaign["id"])
        cleanup = self.store.claim(worker)
        self.assertEqual(cleanup["case_id"], "cleanup-A")
        self.assertEqual(
            cleanup["actors"]["A"]["data"]["owned_installations"], [uid(100)]
        )
        self.finish(worker, cleanup)
        self.assertEqual(restored["status"], "interrupted")

    def test_runner_reconnect_requires_same_label_and_user_and_current_build(self):
        campaign = self.store.create_campaign(self.environment["id"], "preflight")
        self.finish(self.workers["A"], self.store.claim(self.workers["A"]))
        # Queued B is explicit ownership, a third user cannot claim it.
        self.workers["B"]["last_seen"] = time.time() - 100
        self.assertIsNone(
            self.store.resolve_runner("B", self.environment["roles"]["B"])
        )
        wrong = self.store.register(
            self.store.pairing(),
            "B",
            self.workers["A"]["identity"],
            build={"compliance": True, "fingerprint": "x"},
        )
        self.assertIsNone(self.store.claim(self.store.authenticate(wrong["token"])))
        self.store.cancel_campaign(campaign["id"])
        self.assertEqual(campaign["status"], "interrupted")

    def test_source_drift_blocks_new_cases_but_does_not_prevent_cleanup(self):
        campaign = self.store.create_campaign(self.environment["id"], "live")
        for name in ("A", "B"):
            self.finish(
                self.workers[name],
                self.store.claim(self.workers[name]),
                data={"baseline": {}},
            )
        prepare = self.store.claim(self.workers["A"])
        self.workers["A"]["build"]["fingerprint"] = "changed"
        self.finish(
            self.workers["A"], prepare, "passed", {"owned_installations": [uid(99)]}
        )
        self.drain(campaign)
        cleanup = next(
            j for j in self.store.jobs.values() if j.get("case_id") == "cleanup-A"
        )
        self.assertEqual(cleanup["expected_build"], "changed")
        self.assertTrue(
            any(
                c["status"] == "pending" and "build changed" in (c.get("reason") or "")
                for c in campaign["cases"]
            )
        )

    def test_operator_gates_require_named_evidence_cannot_override_automated_or_deferred(
        self,
    ):
        campaign = self.store.create_campaign(self.environment["id"], "live")
        for case in ("prepare-A", "second-platform", "no-such-case"):
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.store.record_campaign_evidence(
                    campaign["id"],
                    case,
                    "manual_pass",
                    "file:///trace",
                    "review",
                    "cdr",
                )
        with self.assertRaises(ValueError):
            self.store.record_campaign_evidence(
                campaign["id"],
                "operator-hosted-auth",
                "manual_pass",
                "",
                "review",
                "cdr",
            )
        self.store.record_campaign_evidence(
            campaign["id"],
            "operator-hosted-auth",
            "manual_pass",
            "/tmp/auth-trace",
            "review",
            "cdr",
        )
        self.assertEqual(
            self.case(campaign, "operator-hosted-auth")["operator_evidence"][
                "operator"
            ],
            "cdr",
        )
        self.assertFalse(campaign["full_ap_acceptance"])

    def test_adapter_uses_selected_cli_interpreter_and_no_shell_or_global_org_change(
        self,
    ):
        with tempfile.TemporaryDirectory() as output:
            job = {
                "profile": "compliance",
                "case_kind": "live",
                "action": "preflight",
                "environment": self.environment,
            }
            argv = command(Path("/platform"), job, Path(output))
            self.assertEqual(argv[0], "/platform/apps/cli/.venv/bin/python")
            self.assertIn("live_cases.py", argv[1])
            self.assertEqual(
                json.loads((Path(output) / "case-input.json").read_text())[
                    "platform_root"
                ],
                "/platform",
            )
        argv = command(
            Path("/platform"),
            {"profile": "compliance", "case_kind": "regression", "action": "contracts"},
            Path("/output"),
        )
        self.assertEqual(argv[2:4], ["--suite", "contracts"])


class ReconnectTests(unittest.TestCase):
    setUp = CampaignTests.setUp

    def test_unclaimed_case_follows_only_same_actor_and_source_reconnect(self):
        campaign = self.store.create_campaign(self.environment["id"], "preflight")
        self.workers["A"]["last_seen"] = time.time() - 100
        old = next(
            j
            for j in self.store.jobs.values()
            if j.get("campaign_id") == campaign["id"]
        )
        registration = self.store.register(
            self.store.pairing(),
            "A",
            self.workers["A"]["identity"],
            build={"compliance": True, "fingerprint": "source-v1"},
        )
        worker = self.store.authenticate(registration["token"])
        claimed = self.store.claim(worker)
        self.assertEqual(claimed["id"], old["id"])
        self.assertEqual(claimed["worker_id"], worker["id"])


if __name__ == "__main__":
    unittest.main()
