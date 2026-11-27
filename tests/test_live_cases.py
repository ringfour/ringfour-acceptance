"""Public HTTP contract tests; no real login, Docker engine or robot is used."""

import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import httpx
    from ringfour_cli.app_manifest import validate_authoring

    from ringfour_acceptance import live_cases as live
except ModuleNotFoundError:
    live = None


def uid(n):
    return str(uuid.UUID(int=n))


@unittest.skipIf(
    live is None, "Use the Platform CLI interpreter for live HTTP contract tests"
)
class LiveCaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name)
        self.requests = []
        self.status = {
            "connectivity": "online",
            "supports_app_uninstall": True,
            "supports_manifest_v2": True,
            "supports_live_settings": True,
            "deployment_namespace": uid(80),
        }
        self.job = {
            "actor_role": "A",
            "case_id": "preflight-A",
            "campaign_id": uid(100),
            "action": "preflight",
            "actors": {},
            "platform_root": str(Path("../ringfour-platform").resolve()),
            "environment": {
                "api_url": "http://test",
                "repository": "localhost:5000/ap",
                "roles": {
                    "A": {"org_id": uid(10), "user_id": uid(1), "robot_id": uid(20)},
                    "B": {"org_id": uid(11), "user_id": uid(2), "robot_id": uid(21)},
                },
            },
        }
        self.membership_user = uid(1)
        self.busy = False
        self.installations = []
        self.run_list = []
        self.installation_states = {}
        self.custom = None
        settings = SimpleNamespace(
            api_url="http://test",
            auth0_domain="test",
            auth0_client_id="test",
            auth0_audience="test",
        )
        self.addCleanup(patch.stopall)
        patch.object(live, "load_settings", return_value=settings).start()
        patch.object(
            live,
            "TokenStore",
            return_value=SimpleNamespace(
                load=lambda settings: {"access_token": "fixture-token"}
            ),
        ).start()
        original = httpx.Client
        patch.object(
            live.httpx,
            "Client",
            side_effect=lambda **kw: original(
                **kw, transport=httpx.MockTransport(self.handler)
            ),
        ).start()

    def handler(self, request):
        self.requests.append(request)
        path = request.url.path
        if self.custom:
            response = self.custom(request)
            if response is not None:
                return response
        if path == "/v1/me":
            body = {
                "user": {"id": self.membership_user},
                "memberships": [{"organization_id": uid(10), "role": "Owner"}],
            }
        elif path == "/v1/robots/" + uid(20):
            body = {"organization_id": uid(10)}
        elif path.endswith("/app-status"):
            body = self.status
        elif path == "/v1/organizations/" + uid(11) + "/robots":
            return httpx.Response(403, json={"code": "forbidden"})
        elif path.endswith("/app-runs"):
            body = {"items": [{"id": uid(40)}] if self.busy else self.run_list}
        elif path.endswith("/app-installations") and request.method == "GET":
            body = {"items": self.installations}
        elif path.endswith("/app-installations") and request.method == "POST":
            body = {"installation_id": uid(50), "operation_id": uid(60)}
        elif path.startswith("/v1/app-installations/"):
            body = self.installation_states.get(
                path.rsplit("/", 1)[1], {"installed": {"revision_id": uid(70)}}
            )
        elif path.startswith("/v1/app-operations/"):
            body = {"state": "succeeded"}
        else:
            raise AssertionError("Unexpected fixture request " + str(request.url))
        return httpx.Response(200, json=body)

    def session(self):
        return live.Session(self.job, self.output)

    def test_identity_and_api_mismatch_stop_before_any_mutation(self):
        self.membership_user = uid(2)
        with self.assertRaises(AssertionError):
            self.session()
        self.assertTrue(all(r.method == "GET" for r in self.requests))
        self.requests.clear()
        self.job["environment"]["api_url"] = "http://another-build"
        with self.assertRaises(live.Pending):
            self.session()
        self.assertEqual(self.requests, [])

    def test_overlay_does_not_select_the_users_global_cli_org(self):
        session = self.session()
        overlay = json.loads(Path(session.cli_env["RINGFOUR_CONFIG"]).read_text())
        self.assertEqual(overlay["selected_org"], uid(10))
        self.assertNotIn("access_token", json.dumps(overlay))
        self.assertNotIn(
            "fixture-token", (self.output / "case-report.json").read_text()
        )

    def test_preflight_refuses_alias_daemons_mismatched_stack_and_busy_robot(self):
        for reason in ("duplicate", "stack", "busy"):
            with self.subTest(reason=reason):
                self.job["actors"] = {}
                self.job["stack_context"] = {}
                self.busy = False
                if reason == "duplicate":
                    self.job["actors"] = {
                        "B": {"data": {"status": copy.deepcopy(self.status)}}
                    }
                if reason == "stack":
                    self.job["stack_context"] = {
                        "robot_id": uid(99),
                        "identity_state": "enrolled",
                    }
                if reason == "busy":
                    self.busy = True
                with self.assertRaises(live.Pending):
                    self.session().preflight()
        self.assertTrue(all(r.method == "GET" for r in self.requests))

    def test_prepare_validates_real_authoring_contract_and_checkpoints_accepted_id_before_poll(
        self,
    ):
        session = self.session()
        validated = []

        def cli(action, *arguments):
            if action == "init":
                project = Path(arguments[0])
                project.mkdir()
                document = json.loads(Path("../robot-check/app.json").read_text())
                (project / "app.json").write_text(json.dumps(document))
            if action == "validate":
                document = json.loads((Path(arguments[0]) / "app.json").read_text())
                validated.append(validate_authoring(document))
            if action == "publish":
                return {"id": uid(70), "app_id": uid(71), "manifest": {}}
            return {}

        def interrupted_poll(accepted):
            saved = json.loads((self.output / "case-report.json").read_text())
            self.assertEqual(saved["data"]["owned_installations"], [uid(50)])
            raise KeyboardInterrupt

        with (
            patch.object(session, "cli", side_effect=cli),
            patch.object(session, "operation", side_effect=interrupted_poll),
            self.assertRaises(KeyboardInterrupt),
        ):
            session.prepare()
        self.assertEqual(len(validated), 1)
        self.assertEqual(session.data["owned_apps"], [uid(71)])
        install = next(r for r in self.requests if r.method == "POST")
        self.assertEqual(json.loads(install.content)["revision_id"], uid(70))

    def test_cleanup_uses_owned_app_ids_not_names_and_preserves_original_bindings(self):
        session = self.session()
        original = {"revision_id": uid(72)}
        self.installations = [
            {"id": uid(51), "app_id": uid(71), "installed": original},
            {"id": uid(52), "app_id": uid(73), "installed": original},
        ]
        self.installation_states[uid(51)] = {
            "installed": original,
            "candidate": {"operation_id": uid(60)},
        }
        session.data.update(owned_apps=[uid(71)], baseline={uid(52): original})
        commands = []

        def cli(action, installation, *arguments):
            commands.append((action, installation))
            self.installation_states[installation] = {"installed": None}
            self.installations[0]["installed"] = None
            self.installations[0]["removed_at"] = "now"
            return {"operation_id": uid(61)}

        with patch.object(session, "cli", side_effect=cli):
            session.cleanup()
        self.assertEqual(commands, [("uninstall", uid(51))])
        self.assertTrue(session.data["cleanup_confirmed"])
        self.assertEqual(session.data["owned_installations"], [uid(51)])

    def test_share_revocation_has_no_json_null_body_and_idempotency_is_case_scoped(
        self,
    ):
        session = self.session()

        def custom(request):
            if request.method == "POST":
                return httpx.Response(200, json={})

        self.custom = custom
        path = "/v1/app-shares/" + uid(81) + "/revoke"
        session.post(path)
        session.post(path)
        posts = [r for r in self.requests if r.method == "POST"]
        self.assertEqual(posts[0].content, b"")
        self.assertEqual(
            posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"]
        )


if __name__ == "__main__":
    unittest.main()
