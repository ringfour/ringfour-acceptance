"""AP live case process: real per-worker CLI identity, public APIs, own fixtures only."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from ringfour_cli.auth import TokenStore
from ringfour_cli.app_project import data_home
from ringfour_cli.config import load_settings

from ringfour_acceptance.diagnostics import scrub
from ringfour_acceptance.templates import cached_template

ACTIVE = {"accepted", "running"}
PROOF = b"ringfour-ap-immutable-evidence-v1"


class Pending(RuntimeError):
    pass


class Session:
    def __init__(self, job, output):
        self.job, self.output = job, output
        self.role = job["actor_role"]
        self.target = job["environment"]["roles"][self.role]
        self.org, self.robot = self.target["org_id"], self.target.get("robot_id")
        self.data = copy.deepcopy(job["actors"].get(self.role, {}).get("data") or {})
        self.data.setdefault("owned_installations", [])
        self.data.setdefault("owned_runs", [])
        self.data.setdefault("owned_shares", [])
        self.data.setdefault("owned_apps", [])
        self.report = {
            "status": "running",
            "action": job["action"],
            "started_at": datetime.now(UTC).isoformat(),
            "checks": [],
            "data": self.data,
            "stack_operations": False,
        }
        self.cli_settings = load_settings()
        expected_api = job["environment"].get("api_url")
        if expected_api and expected_api.rstrip(
            "/"
        ) != self.cli_settings.api_url.rstrip("/"):
            raise Pending(
                "Runner API URL does not match this environment; configure that runner for the build under test"
            )
        tokens = TokenStore().load(self.cli_settings)
        if not tokens or not tokens.get("access_token"):
            raise Pending(
                "Log in with this runner account's CLI; credentials are never borrowed from another runner"
            )
        self.client = httpx.Client(
            base_url=self.cli_settings.api_url,
            headers={"Authorization": "Bearer " + tokens["access_token"]},
            timeout=30,
            trust_env=False,
        )
        self.cli_env = os.environ.copy()
        config = {
            "api_url": self.cli_settings.api_url,
            "auth0_domain": self.cli_settings.auth0_domain,
            "auth0_client_id": self.cli_settings.auth0_client_id,
            "auth0_audience": self.cli_settings.auth0_audience,
            "selected_org": self.org,
        }
        config_path = output / "cli-config.json"
        config_path.write_text(json.dumps(config))
        self.cli_env["RINGFOUR_CONFIG"] = str(config_path)
        self.cli_env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.validate_identity()
        self.checkpoint("Authenticated actor and selected API verified")

    def save(self):
        self.report["data"] = self.data
        pending = self.output / "case-report.json.tmp"
        pending.write_text(json.dumps(self.report, indent=2) + "\n")
        pending.replace(self.output / "case-report.json")

    def checkpoint(self, message):
        self.report["checks"].append(message)
        self.report["stage"] = message
        self.save()
        print(
            f"[{datetime.now(UTC).isoformat()}] {self.role}: {scrub(message)}",
            flush=True,
        )

    def other(self, name=None):
        return (
            self.job["actors"]
            .get(name or ("B" if self.role == "A" else "A"), {})
            .get("data")
            or {}
        )

    def get(self, path, **params):
        response = self.client.get(path, params=params or None)
        if response.status_code == 401:
            raise Pending("Runner login expired; log in again under this account")
        response.raise_for_status()
        return response.json()

    def page(self, path, **params):
        items = []
        while True:
            result = self.get(path, **params)
            items.extend(result["items"])
            if not result.get("next_cursor"):
                return items
            params["cursor"] = result["next_cursor"]

    def post(self, path, body=None, *, expected=(200, 201, 202), label=None):
        key = str(
            uuid.uuid5(
                uuid.UUID(self.job["campaign_id"]),
                self.role + ":" + self.job["case_id"] + ":" + (label or path),
            )
        )
        response = self.client.post(
            path,
            headers={"Idempotency-Key": key},
            **({"json": body} if body is not None else {}),
        )
        if response.status_code == 401:
            raise Pending("Runner login expired; log in again under this account")
        if response.status_code not in expected:
            raise AssertionError(
                f"{path}: expected HTTP {expected}, got {response.status_code}: {scrub(response.text[:1000])}"
            )
        return response.json() if response.content else {}

    def denied(self, path):
        response = self.client.get(path)
        assert response.status_code in (403, 404), (path, response.status_code)
        self.checkpoint("Foreign read refused: " + path)

    def wait(self, read, label, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = read()
            if result:
                return result
            time.sleep(0.25)
        raise AssertionError("Timed out: " + label)

    def wait_for_get(self, path, label, timeout=180):
        def hosted():
            response = self.client.get(path)
            if response.status_code == 404:
                return None
            if response.status_code == 401:
                raise Pending("Runner login expired; log in again under this account")
            response.raise_for_status()
            return response.json()

        return self.wait(hosted, label, timeout)

    def operation(self, accepted):
        operation = self.wait(
            lambda: self._terminal_operation(accepted["operation_id"]),
            "device-confirmed operation",
            300,
        )
        assert operation["state"] == "succeeded", operation
        return operation

    def _terminal_operation(self, operation):
        result = self.get("/v1/app-operations/" + operation)
        return (
            result if result["state"] in ("succeeded", "failed", "timed_out") else None
        )

    def cli(self, *arguments):
        print("CLI " + str(arguments[0]), flush=True)
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "from ringfour_cli.main import app; app()",
                "app",
                *map(str, arguments),
                "--json",
            ],
            cwd=self.job["platform_root"],
            env=self.cli_env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if process.returncode:
            raise AssertionError(
                "CLI "
                + str(arguments[0])
                + ": "
                + scrub(process.stdout[-1800:] + process.stderr[-1800:])
            )
        return json.loads(process.stdout)

    def validate_identity(self):
        me = self.get("/v1/me")
        assert me["user"]["id"] == self.target["user_id"], (
            "Authenticated user UUID differs from the saved role"
        )
        membership = next(
            (m for m in me["memberships"] if m["organization_id"] == self.org), None
        )
        if not membership:
            raise Pending(
                "This account has no membership in the configured organization"
            )
        self.data["user_id"] = me["user"]["id"]
        self.data["membership_role"] = membership["role"]
        self.data["api_url"] = self.cli_settings.api_url
        if self.robot:
            robot = self.get("/v1/robots/" + self.robot)
            assert robot["organization_id"] == self.org, (
                "Robot belongs to a different organization"
            )
            self.data.update(robot_id=self.robot, organization_id=self.org)
        # Do not require/mutate the user's global selected CLI org: children use an overlay.

    def preflight(self):
        if self.role == "Operator":
            if str(self.data["membership_role"]).lower() not in {"operator", "1"}:
                raise Pending(
                    "Configure an actual Operator membership for the restricted-role case"
                )
            return
        if str(self.data["membership_role"]).lower() not in (
            "developer",
            "owner",
            "2",
            "3",
        ):
            raise Pending(
                "Publisher/consumer fixture setup requires Developer or Owner authority"
            )
        if self.role in ("A", "B"):
            other_role = self.job["environment"]["roles"][
                "B" if self.role == "A" else "A"
            ]
            foreign = self.client.get(
                "/v1/organizations/" + other_role["org_id"] + "/robots"
            )
            if foreign.status_code not in (403, 404):
                raise Pending(
                    "A/B test organizations must be mutually private before isolation checks"
                )
        status = self.get("/v1/robots/" + self.robot + "/app-status")
        self.data["status"] = status
        self.save()
        if status["connectivity"] != "online" or not status.get(
            "supports_app_uninstall"
        ):
            raise Pending(
                "Robot must be online and negotiate App uninstall: " + self.robot
            )
        if not status.get("supports_manifest_v2") or not status.get(
            "supports_live_settings"
        ):
            raise Pending(
                "Robot must negotiate manifest v2 and live settings for the AP probe"
            )
        stack = self.job.get("stack_context") or {}
        if (
            stack.get("robot_id")
            and stack.get("identity_state") == "enrolled"
            and stack["robot_id"] != self.robot
        ):
            raise Pending(
                "Selected stack is enrolled as "
                + stack["robot_id"]
                + ", not "
                + self.robot
            )
        self.data["observed_stack"] = copy.deepcopy(stack)
        for component in self.data["observed_stack"].get("components", {}).values():
            container = component.get("container_id", "")
            if re.fullmatch(r"[0-9a-f]{12,64}", container):
                result = subprocess.run(
                    ["docker", "inspect", "--format", "{{.Image}}", container],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if result.returncode == 0 and re.fullmatch(
                    r"sha256:[0-9a-f]{64}", result.stdout.strip()
                ):
                    component["image_id"] = result.stdout.strip()
        for name, actor in self.job["actors"].items():
            peer = actor.get("data") or {}
            if (
                name != self.role
                and peer.get("status", {}).get("deployment_namespace")
                == status.get("deployment_namespace")
                and status.get("deployment_namespace")
            ):
                raise Pending(
                    "Roles "
                    + name
                    + " and "
                    + self.role
                    + " resolve to the same enrolled Daemon namespace"
                )
        runs = self.page(
            f"/v1/organizations/{self.org}/app-runs",
            robot_id=self.robot,
            active_only="true",
        )
        if runs:
            raise Pending(
                "Selected robot has an existing active App; refusing to interfere"
            )
        self.data["baseline"] = {
            item["id"]: item.get("installed")
            for item in self.page(f"/v1/robots/{self.robot}/app-installations")
            if not item.get("removed_at")
        }
        self.data["sensor_readiness"] = (status.get("inventory") or {}).get(
            "state", "unknown"
        )
        self.checkpoint(
            "Robot online/idle, ownership, independent Daemon and baseline bindings verified"
        )
        if self.data["sensor_readiness"] != "available":
            self.checkpoint(
                "Agent inventory is not current; sensor workflows will remain pending until the Agent is reachable"
            )

    def prepare(self):
        pointer = json.loads(
            files("ringfour_cli").joinpath("data/robot-check-template.json").read_text()
        )
        try:
            archive = cached_template(pointer, data_home())
        except (ValueError, OSError) as error:
            raise Pending(str(error)) from error
        project = self.output / "probe"
        repository = (
            (
                self.target.get("repository") or self.job["environment"]["repository"]
            ).rstrip("/")
            + "/ap-"
            + self.job["campaign_id"][:8]
            + "-"
            + self.role.lower()
        )
        self.cli(
            "init",
            project,
            "--app-key",
            "ap-" + self.job["campaign_id"][:12] + "-" + self.role.lower(),
            "--repository",
            repository,
            "--template-archive",
            archive,
        )
        (project / "main.py").write_bytes(
            Path(__file__).with_name("fixtures").joinpath("probe.py").read_bytes()
        )
        document = json.loads((project / "app.json").read_text())
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["probe", "camera"]},
                "label": {"type": "string", "minLength": 1, "maxLength": 80},
            },
            "required": ["mode", "label"],
        }
        self.data["configuration"] = {
            "mode": "probe",
            "label": self.job["campaign_id"] + "-" + self.role,
        }
        document["release"] = "1.0.0"
        document["display"] = {
            "name": "AP probe " + self.role + " " + self.job["campaign_id"][:8],
            "description": "Disposable stationary AP campaign probe",
        }
        document["capabilities"] = {
            "required": [],
            "optional": [{"capability": "cameras"}, {"capability": "imu"}],
        }
        document["configuration"] = {
            "schema": schema,
            "defaults": self.data["configuration"],
            "environment": {},
        }
        document["runtime_settings"] = {
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sample_interval_ms": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 5000,
                    },
                    "gain": {"type": "number", "minimum": -1, "maximum": 1},
                },
                "required": ["sample_interval_ms", "gain"],
            },
            "defaults": {"sample_interval_ms": 250, "gain": 0.25},
        }
        (project / "app.json").write_text(json.dumps(document))
        (project / "config.schema.json").write_text(json.dumps(schema))
        (project / "config.example.json").write_text(
            json.dumps(self.data["configuration"])
        )
        self.cli("validate", project)
        self.cli("build", project)
        revision = self.cli("publish", project)
        self.data["revision"] = revision
        self.data["owned_apps"].append(revision["app_id"])
        self.checkpoint("Private immutable fixture published: " + revision["id"])
        install = self.post(
            f"/v1/robots/{self.robot}/app-installations",
            {
                "revision_id": revision["id"],
                "share_id": None,
                "configuration": self.data["configuration"],
            },
        )
        self.data["installation_id"] = install["installation_id"]
        self.data["owned_installations"].append(install["installation_id"])
        self.checkpoint("Owned installation accepted: " + install["installation_id"])
        self.operation(install)
        self.data["original_binding"] = self.get(
            "/v1/app-installations/" + install["installation_id"]
        )["installed"]
        self.checkpoint("Installation confirmed without a run")

    def prepare_same_org(self):
        source = self.other("A")
        self.data["revision"] = copy.deepcopy(source["revision"])
        self.data["owned_apps"].append(source["revision"]["app_id"])
        self.data["configuration"] = {
            "mode": "probe",
            "label": self.job["campaign_id"] + "-C",
        }
        accepted = self.post(
            f"/v1/robots/{self.robot}/app-installations",
            {
                "revision_id": source["revision"]["id"],
                "share_id": None,
                "configuration": self.data["configuration"],
            },
        )
        self.data["installation_id"] = accepted["installation_id"]
        self.data["owned_installations"].append(accepted["installation_id"])
        self.checkpoint("Same immutable App accepted on the second same-org target")
        self.operation(accepted)

    def foreign(self, runtime=False):
        other = self.other()
        for path in [
            "/v1/robots/" + other["robot_id"],
            "/v1/app-installations/" + other["installation_id"],
            "/v1/apps/" + other["revision"]["app_id"],
            "/v1/app-revisions/" + other["revision"]["id"],
        ]:
            if not runtime:
                self.denied(path)
        if runtime:
            run = other["run_id"]
            for path in [
                f"/v1/app-runs/{run}",
                f"/v1/app-runs/{run}/artifacts/{other['artifact_id']}/content",
                f"/v1/app-runs/{run}/interactions/{other['request_id']}",
            ]:
                self.denied(path)

    def share(self, revoked):
        target = self.job["environment"]["roles"]["B"]["org_id"]
        result = self.post(
            "/v1/app-revisions/" + self.data["revision"]["id"] + "/shares",
            {
                "destination_organization_id": target,
                "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            },
        )
        self.data["owned_shares"].append(result["id"])
        self.data["revoked_share" if revoked else "active_share"] = result["id"]
        self.checkpoint("Destination-scoped share recorded")
        if revoked:
            self.post("/v1/app-shares/" + result["id"] + "/revoke")

    def import_shared(self, revoked=False):
        other = self.other("A")
        body = {
            "revision_id": other["revision"]["id"],
            "share_id": other["revoked_share" if revoked else "active_share"],
        }
        path = f"/v1/organizations/{self.org}/app-imports"
        if revoked:
            self.post(path, body, expected=(403, 404), label="revoked")
            return
        first = self.post(path, body, label="first-import")
        assert self.post(path, body, label="first-import") == first
        assert self.post(path, body, label="second-import") == first
        install = self.post(
            f"/v1/robots/{self.robot}/app-installations",
            body
            | {
                "configuration": {
                    "mode": "probe",
                    "label": "shared-" + self.job["campaign_id"],
                }
            },
            label="shared-install",
        )
        self.data["shared_installation"] = install["installation_id"]
        self.data["shared_import"] = first
        self.data["owned_installations"].append(install["installation_id"])
        self.checkpoint("Consumer installation accepted and owned ID checkpointed")
        self.operation(install)

    def official_publish(self):
        if not self.job["environment"]["official_publisher"]:
            raise Pending(
                "Declare/configure A as the official publisher to run live listed-publication checks"
            )
        manifest = copy.deepcopy(self.data["revision"]["manifest"])
        manifest["app_key"] += "-official"
        manifest["release"] = "1.0.0"
        self.data["official"] = self.post(
            f"/v1/organizations/{self.org}/app-revisions",
            {"manifest": manifest, "visibility": "listed"},
        )
        self.data["owned_apps"].append(self.data["official"]["app_id"])
        self.checkpoint("Official listed revision published")

    def official_discover(self):
        revision = self.other("A")["official"]
        page = self.page(
            "/v1/store/apps",
            organization_id=self.org,
            q=revision["manifest"]["app_key"],
        )
        assert any(item["id"] == revision["app_id"] for item in page)
        body = {"revision_id": revision["id"], "share_id": None}
        first = self.post(
            f"/v1/organizations/{self.org}/app-imports", body, label="official-import"
        )
        assert first == self.post(
            f"/v1/organizations/{self.org}/app-imports",
            body,
            label="official-import-again",
        )

    def start(self, label="probe-start"):
        result = self.cli("start", self.data["installation_id"])
        self.data["owned_runs"].append(result["run_id"])
        if self.data.get("run_id"):
            self.data["previous_run_id"] = self.data["run_id"]
        self.data["run_id"] = result["run_id"]
        self.checkpoint("Own run accepted: " + result["run_id"])
        self.wait(
            lambda: self.get("/v1/app-runs/" + result["run_id"])["state"] == "running",
            "own run started",
        )

    def stop(self):
        run = self.get("/v1/app-runs/" + self.data["run_id"])
        if run["state"] in ACTIVE:
            self.operation(
                self.cli("stop", self.data["installation_id"], "--run", run["id"])
            )
        self.checkpoint("Own run terminal: " + run["id"])

    def events(self, run=None):
        result = self.get(
            "/v1/app-runs/" + (run or self.data["run_id"]) + "/output",
            stream="stdout",
            offset=0,
        )
        events = []
        for line in (
            base64.b64decode(result["data_base64"])
            .decode(errors="replace")
            .splitlines()
        ):
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        return events

    def runtime(self):
        ready = self.wait(
            lambda: next(
                (e for e in self.events() if e.get("event") == "ap.ready"), None
            ),
            "runtime artifact and decision",
        )
        self.data.update(
            artifact_id=ready["artifact_id"], request_id=ready["request_id"]
        )
        self.checkpoint("Artifact and decision IDs recorded")
        receipt = self.wait(
            lambda: next(
                (
                    a
                    for a in self.page(
                        "/v1/app-runs/" + self.data["run_id"] + "/artifacts"
                    )
                    if a["artifact_id"] == ready["artifact_id"]
                ),
                None,
            ),
            "hosted artifact",
        )
        content = self.client.get(
            f"/v1/app-runs/{self.data['run_id']}/artifacts/{ready['artifact_id']}/content"
        )
        content.raise_for_status()
        digest = hashlib.sha256(content.content).hexdigest()
        assert digest == receipt["sha256"] == hashlib.sha256(PROOF).hexdigest()
        self.data["artifact_sha256"] = digest
        self.data["artifact_run_id"] = self.data["run_id"]
        self.wait_for_get(
            f"/v1/app-runs/{self.data['run_id']}/interactions/{ready['request_id']}",
            "hosted run-scoped decision",
        )
        self.wait(
            lambda: any(
                e["type"] == "app.logbook.entry"
                and e.get("run_id") == self.data["run_id"]
                and e.get("payload", {}).get("message") == "AP probe ready"
                for e in self.page(
                    f"/v1/organizations/{self.org}/app-logbook",
                    run_id=self.data["run_id"],
                )
            ),
            "run-scoped author Logbook entry",
        )
        self.checkpoint(
            "Immutable artifact bytes/hash, author Logbook and run-scoped decision verified"
        )

    def settings(self):
        installation = self.data["installation_id"]
        values = self.output / "values.json"
        values.write_text('{"sample_interval_ms":250,"gain":-0.375}')
        path = "/v1/app-installations/" + installation + "/settings"
        before = self.get(path)

        def write_body(state, value):
            return {
                "values": value,
                "expected_selected_revision_id": (
                    state.get("selected_for_next_start") or {}
                ).get("id"),
                "expected_applied_revision_id": (state.get("last_applied") or {}).get(
                    "id"
                ),
                "expected_run_id": state.get("active_run_id"),
            }

        self.post(
            path,
            write_body(before, {"sample_interval_ms": 250, "gain": 9}),
            expected=(400,),
            label="invalid-gain",
        )
        assert self.get(path) == before, "Invalid edit changed settings state"
        self.cli("settings", "set", installation, "--file", values)
        applied = self.wait(
            lambda: (
                r
                if ((r := self.get(path)).get("last_applied") or {})
                .get("values", {})
                .get("gain")
                == -0.375
                else None
            ),
            "fraction applied",
        )
        self.wait(
            lambda: any(
                e.get("event") == "ap.settings" and e.get("gain") == -0.375
                for e in self.events()
            ),
            "actual settings handler",
        )
        self.post(
            path,
            write_body(before, {"sample_interval_ms": 250, "gain": 0.1}),
            expected=(409,),
            label="stale-settings-cas",
        )
        values.write_text('{"sample_interval_ms":777,"gain":0.5}')
        pending = self.cli("settings", "set", installation, "--file", values)
        self.wait(
            lambda: self.get(path).get("pending_application") is None,
            "rejected edit resolved",
        )
        assert self.get(path)["last_applied"]["values"]["gain"] == -0.375
        self.data["fraction_application"] = applied["last_applied"]["id"]
        self.data["rejected_application"] = pending.get("application_id")
        self.checkpoint(
            "Fraction reached the App; invalid edit and stale CAS refused; rejected edit left applied value intact"
        )

    def decision(self):
        path = (
            f"/v1/app-runs/{self.data['run_id']}/interactions/{self.data['request_id']}"
        )
        self.post(path + "/responses", {"chosen_value": "yes"}, label="first-answer")
        self.post(
            path + "/responses",
            {"chosen_value": "no"},
            expected=(409,),
            label="competing-answer",
        )
        result = self.wait(
            lambda: (
                r
                if ((r := self.get(path)).get("response") or {}).get("status")
                == "applied"
                else None
            ),
            "answer applied",
        )
        assert result["response"]["actor_user_id"] == self.target["user_id"]
        self.checkpoint("One response applied with the real actor UUID")

    def configure(self, mode):
        resource = "/v1/app-installations/" + self.data["installation_id"]
        binding = self.get(resource)["installed"]
        configuration = self.data["configuration"] | {"mode": mode}
        accepted = self.post(
            resource + "/configure",
            {
                "expected_configuration_revision_id": binding[
                    "configuration_revision_id"
                ],
                "configuration": configuration,
            },
            label="configure-" + mode,
        )
        self.operation(accepted)
        self.data["configuration"] = configuration
        self.save()

    def lifecycle(self):
        resource = "/v1/app-installations/" + self.data["installation_id"]
        original = self.get(resource)["installed"]
        manifest = copy.deepcopy(self.data["revision"]["manifest"])
        manifest["release"] = "2.0.0"
        revision = self.post(
            f"/v1/organizations/{self.org}/app-revisions",
            {"manifest": manifest, "visibility": "unlisted"},
        )
        expected = {
            "expected_revision_id": original["revision_id"],
            "expected_configuration_revision_id": original["configuration_revision_id"],
        }
        self.post(resource + "/uninstall", expected, expected=(409,))
        self.post(
            resource + "/update",
            expected
            | {
                "revision_id": revision["id"],
                "share_id": None,
                "configuration": self.data["configuration"],
            },
            expected=(409,),
        )
        self.stop()
        self.operation(self.cli("update", self.data["installation_id"], revision["id"]))
        assert self.get(resource)["installed"]["revision_id"] == revision["id"]
        assert not self.page(
            f"/v1/organizations/{self.org}/app-runs",
            robot_id=self.robot,
            active_only="true",
        )
        self.operation(
            self.cli(
                "rollback",
                self.data["installation_id"],
                "--revision",
                original["revision_id"],
                "--configuration-revision",
                original["configuration_revision_id"],
            )
        )
        restored = self.get(resource)["installed"]
        assert all(
            restored[k] == original[k]
            for k in ("revision_id", "configuration_revision_id", "image")
        )
        self.operation(self.cli("uninstall", self.data["installation_id"], "--yes"))
        assert self.get(resource)["installed"] is None
        content = self.client.get(
            f"/v1/app-runs/{self.data['artifact_run_id']}/artifacts/{self.data['artifact_id']}/content"
        )
        content.raise_for_status()
        assert (
            hashlib.sha256(content.content).hexdigest() == self.data["artifact_sha256"]
        )
        replacement = self.post(
            f"/v1/robots/{self.robot}/app-installations",
            {
                "revision_id": original["revision_id"],
                "share_id": None,
                "configuration": self.data["configuration"],
            },
            label="reinstall",
        )
        self.data["owned_installations"].append(replacement["installation_id"])
        self.checkpoint("Replacement accepted and checkpointed")
        self.operation(replacement)
        assert replacement["installation_id"] != self.data["installation_id"]
        self.data["installation_id"] = replacement["installation_id"]
        self.checkpoint(
            "Idle update/rollback, retained evidence, uninstall and fresh reinstall identity verified"
        )

    def require_sensor(self, capability):
        status = self.get("/v1/robots/" + self.robot + "/app-status")
        inventory = status.get("inventory") or {}
        if inventory.get("state") != "available":
            raise Pending(
                "Current Agent inventory is unavailable: "
                + str(inventory.get("reason") or "unknown reason")
            )
        if capability not in (inventory.get("current") or {}).get("capabilities", []):
            raise Pending("Selected robot does not currently advertise " + capability)

    def camera(self):
        self.require_sensor("cameras")
        self.configure("camera")
        self.start()
        self.wait(
            lambda: (
                self.get("/v1/app-runs/" + self.data["run_id"])["state"] not in ACTIVE
            ),
            "finite camera run",
        )
        run = self.get("/v1/app-runs/" + self.data["run_id"])
        assert run["state"] == "succeeded", run
        photos = self.wait(
            lambda: (
                [
                    a
                    for a in self.page(
                        "/v1/app-runs/" + self.data["run_id"] + "/artifacts"
                    )
                    if a["path"].endswith(".png")
                ]
                or None
            ),
            "camera photos",
        )
        photos = self.wait(
            lambda: (
                a
                if len(
                    a := [
                        x
                        for x in self.page(
                            "/v1/app-runs/" + self.data["run_id"] + "/artifacts"
                        )
                        if x["path"].endswith(".png")
                    ]
                )
                == 2
                else None
            ),
            "two camera receipts",
        )
        for photo in photos:
            content = self.client.get(
                f"/v1/app-runs/{self.data['run_id']}/artifacts/{photo['artifact_id']}/content"
            )
            content.raise_for_status()
            assert (
                content.content.startswith(b"\x89PNG\r\n\x1a\n")
                and hashlib.sha256(content.content).hexdigest() == photo["sha256"]
            )
        self.data["photos"] = [
            {"artifact_id": p["artifact_id"], "sha256": p["sha256"]} for p in photos
        ]
        self.checkpoint(
            "Two stationary camera PNG captures downloaded and hash-verified"
        )

    def robot_check(self):
        self.require_sensor("imu")
        path = self.output / "robot-check-workflow.json"
        args = [
            sys.executable,
            str(Path(self.job["platform_root"]) / "tests/e2e/apps-live-acceptance.py"),
            "--org",
            self.org,
            "--robot",
            self.robot,
            "--repository",
            self.job["environment"]["repository"].rstrip("/")
            + "/imu-"
            + self.job["campaign_id"][:8]
            + "-"
            + self.role.lower(),
            "--output",
            str(path),
        ]
        entry = self.job["environment"].get("robot_check_entrypoint")
        if entry:
            args += ["--entrypoint", entry]
        try:
            child = subprocess.Popen(
                args, cwd=self.job["platform_root"], env=self.cli_env
            )
            try:
                returncode = child.wait(timeout=1500)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                # Let the adapter stop its child and finish owned-fixture cleanup.
                child.send_signal(signal.SIGINT)
                child.wait()
                raise
        finally:
            if path.exists():
                self.data["robot_check"] = json.loads(path.read_text())
            progress = self.output / "live-imu-settings.json"
            records = [
                self.data.get("robot_check") or {},
                json.loads(progress.read_text()) if progress.exists() else {},
            ]
            for record in records:
                for key in ("installation_id", "replacement_installation_id"):
                    if (
                        record.get(key)
                        and record[key] not in self.data["owned_installations"]
                    ):
                        self.data["owned_installations"].append(record[key])
                if (
                    record.get("run_id")
                    and record["run_id"] not in self.data["owned_runs"]
                ):
                    self.data["owned_runs"].append(record["run_id"])
                if (
                    record.get("app_id")
                    and record["app_id"] not in self.data["owned_apps"]
                ):
                    self.data["owned_apps"].append(record["app_id"])
            self.save()
        assert returncode == 0 and self.data.get("robot_check", {}).get("passed"), (
            "Reference Robot Check did not pass; inspect its separate workflow/cleanup report"
        )

    def latency(self):
        report = self.output / "live-latency.json"
        result = subprocess.run(
            [
                sys.executable,
                str(Path(self.job["platform_root"]) / "tests/e2e/ap19-live-reads.py"),
                "--output",
                str(report),
            ],
            cwd=self.job["platform_root"],
            env=self.cli_env,
            check=False,
            timeout=300,
        )
        assert result.returncode == 0, "Read-only deployment latency sampling failed"
        self.data["latency_report"] = json.loads(report.read_text())
        self.checkpoint(
            "Authenticated deployment latency recorded; no 10-robot/100-App performance claim"
        )

    def cleanup(self):
        # Recover an accepted response lost at interruption using exact owned App
        # and installation identities, never a robot name or unrelated run.
        apps = set(self.data.get("owned_apps", []))
        if self.robot and apps:
            for item in self.page(f"/v1/robots/{self.robot}/app-installations"):
                if (
                    item.get("app_id") in apps
                    and item["id"] not in self.data["owned_installations"]
                ):
                    self.data["owned_installations"].append(item["id"])
        owned = set(self.data.get("owned_installations", []))
        if self.robot and owned:
            for run in self.page(
                f"/v1/organizations/{self.org}/app-runs", robot_id=self.robot
            ):
                if run["installation_id"] in owned and run["state"] in ACTIVE:
                    self.operation(
                        self.cli("stop", run["installation_id"], "--run", run["id"])
                    )
                    self.checkpoint("Cleanup stopped owned run " + run["id"])
        for run_id in self.data.get("owned_runs", []):
            run = self.get("/v1/app-runs/" + run_id)
            if run["state"] in ACTIVE:
                self.operation(
                    self.cli("stop", run["installation_id"], "--run", run_id)
                )
        for installation in list(owned):
            state = self.get("/v1/app-installations/" + installation)
            candidate = state.get("candidate") or {}
            if candidate.get("operation_id"):
                self.wait(
                    lambda operation_id=candidate["operation_id"]: (
                        self._terminal_operation(operation_id)
                    ),
                    "owned pending installation settles",
                    660,
                )
                state = self.get("/v1/app-installations/" + installation)
            if state.get("installed") is not None:
                self.operation(self.cli("uninstall", installation, "--yes"))
            self.checkpoint("Cleanup removed owned installation " + installation)
        for share in self.data.get("owned_shares", []):
            self.post("/v1/app-shares/" + share + "/revoke", label="cleanup-" + share)
        if self.robot:
            current = {
                item["id"]: item.get("installed")
                for item in self.page(f"/v1/robots/{self.robot}/app-installations")
                if not item.get("removed_at")
            }
            for identity, binding in self.data.get("baseline", {}).items():
                assert current.get(identity) == binding, (
                    "Pre-existing installation binding changed"
                )
        self.data["retained_catalog_evidence"] = {
            "apps": self.data.get("owned_apps", []),
            "note": "Published immutable revisions/imports and terminal run evidence are retained; the public API has no catalog deletion operation.",
        }
        self.data["cleanup_confirmed"] = True
        self.checkpoint(
            "Owned-fixture cleanup confirmed; original installation bindings preserved"
        )

    def run(self):
        action = self.job["action"]
        actions = {
            "preflight": self.preflight,
            "latency": self.latency,
            "prepare": self.prepare,
            "prepare-same-org": self.prepare_same_org,
            "foreign": self.foreign,
            "foreign-runtime": lambda: self.foreign(True),
            "share-revoke": lambda: self.share(True),
            "revoked-import": lambda: self.import_shared(True),
            "share-active": lambda: self.share(False),
            "import-shared": self.import_shared,
            "official-publish": self.official_publish,
            "official-discover": self.official_discover,
            "start": self.start,
            "restart": self.start,
            "stop": self.stop,
            "runtime": self.runtime,
            "settings": self.settings,
            "decision": self.decision,
            "lifecycle": self.lifecycle,
            "camera": self.camera,
            "robot-check": self.robot_check,
            "cleanup": self.cleanup,
        }
        if action in actions:
            actions[action]()
            return
        if action == "revoke-active":
            self.post("/v1/app-shares/" + self.data["active_share"] + "/revoke")
        elif action == "share-retained":
            imports = self.page(f"/v1/organizations/{self.org}/app-imports")
            assert any(
                i["app_id"] == self.other("A")["revision"]["app_id"] for i in imports
            )
            self.post(
                f"/v1/organizations/{self.org}/app-imports",
                {
                    "revision_id": self.other("A")["revision"]["id"],
                    "share_id": self.other("A")["active_share"],
                },
                label="retained-import",
            )
            assert (
                self.get("/v1/app-installations/" + self.data["shared_installation"])[
                    "installed"
                ]["revision_id"]
                == self.other("A")["revision"]["id"]
            )
        elif action == "listed-denial":
            self.post(
                f"/v1/organizations/{self.org}/app-revisions",
                {"manifest": self.data["revision"]["manifest"], "visibility": "listed"},
                expected=(403,),
            )
        elif action == "slot":
            resource = "/v1/app-installations/" + self.data["shared_installation"]
            binding = self.get(resource)["installed"]
            self.post(
                resource + "/start",
                {
                    "expected_revision_id": binding["revision_id"],
                    "expected_configuration_revision_id": binding[
                        "configuration_revision_id"
                    ],
                },
                expected=(409,),
            )
        elif action == "settings-isolation":
            assert (
                self.get(
                    "/v1/app-installations/"
                    + self.data["installation_id"]
                    + "/settings"
                )["last_applied"]["values"]["gain"]
                == 0.25
            )
        elif action == "continues":
            assert self.get("/v1/app-runs/" + self.data["run_id"])["state"] == "running"
            self.wait(
                lambda: any(e.get("event") == "ap.tick" for e in self.events()),
                "A continues producing output",
            )
        elif action == "stale-stop":
            self.post(
                "/v1/app-installations/" + self.data["installation_id"] + "/stop",
                {"expected_run_id": self.data["previous_run_id"]},
                expected=(409,),
            )
            assert self.get("/v1/app-runs/" + self.data["run_id"])["state"] == "running"
        elif action == "same-org":
            other = self.other("A")
            assert (
                self.org == self.job["environment"]["roles"]["A"]["org_id"]
                and self.robot != other["robot_id"]
            )
            assert (
                self.get("/v1/app-installations/" + self.data["installation_id"])[
                    "robot_id"
                ]
                == self.robot
            )
            assert (
                self.get("/v1/app-installations/" + other["installation_id"])[
                    "robot_id"
                ]
                == other["robot_id"]
            )
            assert (
                self.get("/v1/app-installations/" + self.data["installation_id"])[
                    "installed"
                ]["revision_id"]
                == other["revision"]["id"]
            )
            self.start()
            path = "/v1/app-installations/" + self.data["installation_id"] + "/settings"
            self.wait(
                lambda: (
                    (self.get(path).get("last_applied") or {})
                    .get("values", {})
                    .get("gain")
                    == 0.25
                ),
                "second target defaults",
            )
            values = self.output / "same-org-settings.json"
            values.write_text('{"sample_interval_ms":250,"gain":0.125}')
            self.cli("settings", "set", self.data["installation_id"], "--file", values)
            self.wait(
                lambda: (
                    (self.get(path).get("last_applied") or {})
                    .get("values", {})
                    .get("gain")
                    == 0.125
                ),
                "second target own settings",
            )
            a_settings = self.get(
                "/v1/app-installations/" + other["installation_id"] + "/settings"
            )
            assert (a_settings.get("selected_for_next_start") or {}).get(
                "values", {}
            ).get("gain") != 0.125
            self.stop()
            self.checkpoint(
                "Same immutable App on two same-org robots has distinct bindings and isolated settings"
            )
        elif action == "operator-permissions":
            source = self.other("A")
            resource = "/v1/app-installations/" + source["installation_id"]
            binding = self.get(resource)["installed"]
            self.post(
                resource + "/configure",
                {
                    "expected_configuration_revision_id": binding[
                        "configuration_revision_id"
                    ],
                    "configuration": source["configuration"],
                },
                expected=(403,),
            )
            self.post(
                f"/v1/organizations/{self.org}/app-revisions",
                {"manifest": source["revision"]["manifest"], "visibility": "unlisted"},
                expected=(403,),
            )
            start = self.cli("start", source["installation_id"])
            self.data["owned_runs"].append(start["run_id"])
            self.checkpoint("Operator own fixture run ID recorded")
            self.wait(
                lambda: (
                    self.get("/v1/app-runs/" + start["run_id"])["state"] == "running"
                ),
                "Operator allowed start",
            )
            self.operation(
                self.cli("stop", source["installation_id"], "--run", start["run_id"])
            )
        else:
            raise ValueError("Unknown live case action")
        self.checkpoint("Case assertions passed: " + action)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    job = json.loads(args.input.read_text())
    session = None
    code = 0
    try:
        session = Session(job, args.output)
        session.run()
        session.report["status"] = "passed"
    except Pending as error:
        code = 78
        if session:
            session.report.update(status="pending", error=scrub(str(error)))
        else:
            (args.output / "case-report.json").write_text(
                json.dumps(
                    {
                        "status": "pending",
                        "error": scrub(str(error)),
                        "data": job["actors"]
                        .get(job["actor_role"], {})
                        .get("data", {}),
                    }
                )
            )
        print("Prerequisite pending: " + scrub(str(error)), flush=True)
    except BaseException as error:  # noqa: BLE001 - checkpoint interruption before exiting this worker case
        code = 130 if isinstance(error, KeyboardInterrupt) else 1
        if session:
            session.report.update(
                status="interrupted" if code == 130 else "failed",
                error=scrub(str(error)),
            )
        else:
            (args.output / "case-report.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error": scrub(str(error)),
                        "data": job["actors"]
                        .get(job["actor_role"], {})
                        .get("data", {}),
                    }
                )
            )
        print(f"{type(error).__name__}: " + scrub(str(error)), flush=True)
    finally:
        if session:
            session.report["completed_at"] = datetime.now(UTC).isoformat()
            session.save()
            session.client.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
