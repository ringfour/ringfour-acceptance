"""Durable, credential-free AP campaign planning and worker coordination."""

import copy
import json
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .cases import CATALOG_VERSION, catalog, make_plan

DONE = {
    "passed",
    "manual_pass",
    "failed",
    "pending",
    "blocked",
    "interrupted",
    "deferred",
    "requires_operator",
}
JOB_DONE = {"passed", "failed", "pending", "interrupted"}
ROLES = ("A", "B", "C", "Operator")


def stamp():
    return datetime.now(UTC).isoformat()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(data, indent=2) + "\n")
    pending.replace(path)


def expected_role(identity, role):
    if (identity.get("user") or {}).get("id") != role["user_id"]:
        raise ValueError(
            "Runner's authenticated user does not match the configured user UUID"
        )
    org = next(
        (o for o in identity.get("organizations", []) if o["id"] == role["org_id"]),
        None,
    )
    if not org:
        raise ValueError("Runner cannot access the configured organization")
    if role.get("robot_id") and not any(
        r["id"] == role["robot_id"] for r in org.get("robots", [])
    ):
        raise ValueError("Configured robot is not owned by this organization")


class ComplianceStore:
    def init_compliance(self):
        self.environments, self.campaigns = {}, {}
        file = self.root / "environments.json"
        if file.exists():
            self.environments = json.loads(file.read_text())
        for path in self.root.glob("campaigns/*/campaign.json"):
            campaign = json.loads(path.read_text())
            if campaign["status"] in {"running", "waiting_runner", "cancelling"}:
                campaign.update(
                    status="interrupted",
                    reason="Panel restarted; inspect runner cleanup and explicitly recover cleanup before retrying.",
                )
                for case in campaign["cases"]:
                    if case["status"] not in DONE:
                        case.update(
                            status="interrupted", reason="Interrupted by panel restart"
                        )
                self.save_campaign(campaign)
            self.campaigns[campaign["id"]] = campaign

    def compliance_public(self):
        return {
            "environments": list(self.environments.values()),
            "campaigns": sorted(
                self.campaigns.values(), key=lambda c: c["created_at"], reverse=True
            ),
            "case_catalog": catalog(),
        }

    def save_environment(self, data):
        roles = {}
        for name, source in (data.get("roles") or {}).items():
            if name not in ROLES:
                raise ValueError("Unknown test role")
            if not source:
                continue
            role = {
                key: str(source.get(key) or "").strip()
                for key in (
                    "runner_label",
                    "user_id",
                    "org_id",
                    "robot_id",
                    "stack_id",
                    "repository",
                )
            }
            if not role["runner_label"]:
                raise ValueError(f"{name}: choose a runner")
            for key in ("user_id", "org_id"):
                uuid.UUID(role[key])
            if name != "Operator" or role["robot_id"]:
                uuid.UUID(role["robot_id"])
            if role["repository"] and (
                any(c.isspace() for c in role["repository"])
                or role["repository"].startswith("-")
            ):
                raise ValueError("Registry repository cannot contain spaces/options")
            roles[name] = role
        if "A" not in roles or "B" not in roles:
            raise ValueError("Configure publisher A and consumer B")
        if (
            roles["A"]["user_id"] == roles["B"]["user_id"]
            or roles["A"]["org_id"] == roles["B"]["org_id"]
        ):
            raise ValueError("A/B must be different real users and organizations")
        robots = [r["robot_id"] for n, r in roles.items() if n != "Operator"]
        if len(robots) != len(set(robots)):
            raise ValueError("Each robot role needs a distinct robot UUID")
        stacks = [
            r["stack_id"] for n, r in roles.items() if n != "Operator" and r["stack_id"]
        ]
        if len(stacks) != len(set(stacks)):
            raise ValueError(
                "Two robot roles cannot use the same enrolled Daemon/stack"
            )
        if "C" in roles and roles["C"]["org_id"] != roles["A"]["org_id"]:
            raise ValueError("C is the optional second robot in A's organization")
        if "Operator" in roles and roles["Operator"]["org_id"] != roles["A"]["org_id"]:
            raise ValueError("Operator must belong to A's disposable test organization")
        repository = str(data.get("repository") or "").strip()
        if (
            not repository
            or any(c.isspace() for c in repository)
            or repository.startswith("-")
        ):
            raise ValueError("Set a registry repository prefix")
        api_url = str(data.get("api_url") or "http://localhost:8080").rstrip("/")
        parsed = urlsplit(api_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Set an HTTP(S) API origin without credentials, query or fragment"
            )
        name = str(data.get("name") or "Virtual AP environment").strip()[:120]
        environment = {
            "id": data.get("id") or str(uuid.uuid4()),
            "name": name,
            "regression_runner": str(
                data.get("regression_runner") or roles["A"]["runner_label"]
            ),
            "repository": repository,
            "api_url": api_url,
            "roles": roles,
            "official_publisher": data.get("official_publisher") is True,
            "robot_check_entrypoint": str(
                data.get("robot_check_entrypoint") or ""
            ).strip(),
            "build_label": str(data.get("build_label") or "Local build").strip()[:200],
            "updated_at": stamp(),
        }
        uuid.UUID(environment["id"])
        with self.lock:
            self.environments[environment["id"]] = environment
            atomic(self.root / "environments.json", self.environments)
        return environment

    def resolve_runner(self, label, role=None):
        candidates = [
            w
            for w in self.workers.values()
            if w["label"] == label and time.time() - w["last_seen"] < 15
        ]
        if role:
            candidates = [
                w
                for w in candidates
                if (w["identity"].get("user") or {}).get("id") == role["user_id"]
            ]
        if len(candidates) != 1:
            return None
        return candidates[0]

    def create_campaign(self, environment_id, scope):
        if scope not in {"all", "regression", "preflight", "live"}:
            raise ValueError("Choose all, regression, preflight or live")
        with self.lock:
            if any(
                c["status"] in {"running", "waiting_runner", "cancelling"}
                for c in self.campaigns.values()
            ):
                raise ValueError("Finish/cancel the current AP campaign first")
            environment = copy.deepcopy(self.environments[environment_id])
            campaign = {
                "id": str(uuid.uuid4()),
                "catalog_version": CATALOG_VERSION,
                "scope": scope,
                "environment": environment,
                "created_at": stamp(),
                "status": "running",
                "cancel_requested": False,
                "cases": make_plan(environment, scope),
                "actors": {},
                "builds": {},
                "jobs": [],
                "full_ap_acceptance": False,
            }
            for role in ROLES:
                campaign["actors"][role] = {"data": {}}
            self.campaigns[campaign["id"]] = campaign
            self.save_campaign(campaign)
            self.advance_campaigns()
            return campaign

    def save_campaign(self, campaign):
        counts = {}
        for case in campaign["cases"]:
            counts[case["status"]] = counts.get(case["status"], 0) + 1
        campaign["summary"] = counts
        campaign["full_ap_acceptance"] = (
            False  # AP-16/hosted/operator evidence remains explicit.
        )
        required = [c for c in campaign["cases"] if c["status"] != "deferred"]
        campaign["all_applicable_passed"] = bool(required) and all(
            c["status"] in {"passed", "manual_pass"} for c in required
        )
        automated = [c for c in campaign["cases"] if c["kind"] != "operator"]
        campaign["automated_passed"] = bool(automated) and all(
            c["status"] == "passed" for c in automated
        )
        folder = self.root / "campaigns" / campaign["id"]
        atomic(folder / "campaign.json", campaign)
        lines = [
            "# Ringfour AP campaign",
            "",
            campaign["environment"]["name"],
            "",
            f"Build: {campaign['environment']['build_label']} · scope: {campaign['scope']} · status: {campaign['status']}",
            "",
            "Evidence is scoped to the recorded runners/builds. Operator and deferred gates are separate.",
            "",
            "| Case | AP | Kind | Result | Evidence / reason |",
            "|---|---|---|---|---|",
        ]
        for case in campaign["cases"]:
            detail = (
                case.get("reason")
                or case.get("operator_evidence", {}).get("reference")
                or case.get("job_id", "")
            )
            lines.append(
                f"| {case['title']} | {case['ap']} | {case['kind']} | {case['status']} | {str(detail).replace('|', '/')} |"
            )
        (folder / "report.md").write_text("\n".join(lines) + "\n")

    def advance_campaigns(self):
        """Create at most one runnable job. Called under the Store RLock."""
        with self.lock:
            if any(
                j["status"] in {"queued", "running", "stopping"}
                for j in self.jobs.values()
            ):
                return
            for campaign in self.campaigns.values():
                if campaign["status"] not in {
                    "running",
                    "waiting_runner",
                    "cancelling",
                }:
                    continue
                cases = campaign["cases"]
                changed, waiting_runner = False, False
                for case in cases:
                    if case["status"] not in {"waiting", "waiting_runner"}:
                        continue
                    if campaign["cancel_requested"] and case["kind"] != "cleanup":
                        case.update(status="interrupted", reason="Campaign cancelled")
                        changed = True
                        continue
                    if case["kind"] == "cleanup" and any(
                        c["kind"] not in {"cleanup", "operator"}
                        and c["status"] not in DONE
                        for c in cases
                    ):
                        continue
                    dependencies = [
                        next(c for c in cases if c["id"] == identity)
                        for identity in case["dependencies"]
                    ]
                    if any(c["status"] not in DONE for c in dependencies):
                        continue
                    if not case["always"] and any(
                        c["status"] != "passed" for c in dependencies
                    ):
                        case.update(
                            status="blocked",
                            reason="Prerequisite did not pass: "
                            + ", ".join(
                                c["id"] for c in dependencies if c["status"] != "passed"
                            ),
                        )
                        changed = True
                        continue
                    role = campaign["environment"]["roles"].get(case["role"])
                    if case["kind"] != "regression" and not role:
                        case.update(
                            status="passed" if case["kind"] == "cleanup" else "pending",
                            reason="No fixtures to clean"
                            if case["kind"] == "cleanup"
                            else f"Configure role {case['role']} to run this case",
                        )
                        changed = True
                        continue
                    if (
                        case["kind"] == "cleanup"
                        and not campaign["actors"][case["role"]]["data"]
                    ):
                        case.update(
                            status="passed",
                            reason="No fixtures were created for this role",
                        )
                        changed = True
                        continue
                    label = (
                        campaign["environment"]["regression_runner"]
                        if case["kind"] == "regression"
                        else role["runner_label"]
                    )
                    worker = self.resolve_runner(
                        label, role if case["kind"] != "regression" else None
                    )
                    if not worker:
                        case.update(
                            status="waiting_runner",
                            reason=f"Connect exactly one runner labelled {label}"
                            + (f" as user {role['user_id']}" if role else ""),
                        )
                        waiting_runner = True
                        changed = True
                        continue
                    if not worker.get("build", {}).get("compliance"):
                        case.update(
                            status="waiting_runner",
                            reason="Reconnect this runner using the updated AP campaign worker",
                        )
                        waiting_runner = True
                        changed = True
                        continue
                    build = copy.deepcopy(worker["build"])
                    fingerprint = build.get("fingerprint")
                    previous = campaign["builds"].get(label)
                    if (
                        previous
                        and previous.get("fingerprint") != fingerprint
                        and case["kind"] != "cleanup"
                    ):
                        case.update(
                            status="pending",
                            reason="Runner source build changed during this campaign; start a new campaign for the new build",
                        )
                        changed = True
                        continue
                    campaign["builds"].setdefault(label, build)
                    job = {
                        "id": str(uuid.uuid4()),
                        "group_id": campaign["id"],
                        "profile": "compliance",
                        "worker_id": worker["id"],
                        "worker_label": worker["label"],
                        "target": role,
                        "campaign_id": campaign["id"],
                        "case_id": case["id"],
                        "case_title": case["title"],
                        "case_kind": case["kind"],
                        "action": case["action"],
                        "expected_build": fingerprint,
                        "environment": copy.deepcopy(campaign["environment"]),
                        "actor_role": case["role"],
                        "actors": copy.deepcopy(campaign["actors"]),
                        "stack_context": copy.deepcopy(
                            self.stacks.get(role.get("stack_id")) or {}
                        )
                        if role
                        else {},
                        "status": "queued",
                        "created_at": stamp(),
                        "cancel_requested": False,
                        "progress": {},
                        "files": {},
                        "log": "",
                    }
                    self.jobs[job["id"]] = job
                    self.save(job)
                    case.update(status="queued", job_id=job["id"], reason=None)
                    campaign["jobs"].append(job["id"])
                    campaign["status"] = (
                        "cancelling" if campaign["cancel_requested"] else "running"
                    )
                    self.save_campaign(campaign)
                    return
                if changed:
                    self.save_campaign(campaign)
                if all(c["status"] in DONE for c in cases):
                    if campaign["cancel_requested"]:
                        campaign["status"] = "interrupted"
                    elif any(c["status"] == "failed" for c in cases):
                        campaign["status"] = "failed"
                    elif all(
                        c["status"] in {"passed", "manual_pass", "deferred"}
                        for c in cases
                    ):
                        campaign["status"] = "passed"
                    else:
                        campaign["status"] = "pending"
                    campaign["finished_at"] = stamp()
                    self.save_campaign(campaign)
                elif waiting_runner:
                    campaign["status"] = "waiting_runner"
                    self.save_campaign(campaign)

    def rebind_campaign_jobs(self, worker):
        """An unclaimed job may follow an identically authenticated reconnect only."""
        for job in list(self.jobs.values()):
            if (
                not job.get("campaign_id")
                or job["status"] != "queued"
                or job["worker_id"] == worker["id"]
            ):
                continue
            previous = self.workers.get(job["worker_id"])
            if previous and time.time() - previous["last_seen"] < 15:
                continue
            role = job.get("target")
            if self.resolve_runner(job["worker_label"], role) is not worker:
                continue
            if not worker.get("build", {}).get("compliance"):
                continue
            if (
                job["case_kind"] != "cleanup"
                and worker["build"].get("fingerprint") != job["expected_build"]
            ):
                job.update(
                    status="pending",
                    reason="Reconnected runner uses a different build; start a new campaign",
                    finished_at=stamp(),
                )
                self.save(job)
                self.campaign_job_update(job)
                continue
            job["worker_id"] = worker["id"]
            if job["case_kind"] == "cleanup":
                job["expected_build"] = worker["build"].get("fingerprint")
            self.save(job)

    def campaign_job_update(self, job):
        if not job.get("campaign_id"):
            return
        campaign = self.campaigns[job["campaign_id"]]
        case = next(c for c in campaign["cases"] if c["id"] == job["case_id"])
        report = job.get("progress", {}).get("case-report") or {}
        if (
            isinstance(report.get("data"), dict)
            and job["actor_role"] in campaign["actors"]
        ):
            campaign["actors"][job["actor_role"]]["data"] = copy.deepcopy(
                report["data"]
            )
        if job["status"] in JOB_DONE:
            case.update(
                status=job["status"],
                finished_at=stamp(),
                reason=report.get("error") or job.get("reason"),
            )
        else:
            case["status"] = "running"
        self.save_campaign(campaign)

    def cancel_campaign(self, campaign_id):
        with self.lock:
            campaign = self.campaigns[campaign_id]
            campaign.update(cancel_requested=True, status="cancelling")
            for job_id in campaign["jobs"]:
                job = self.jobs[job_id]
                if job["status"] not in JOB_DONE:
                    self.cancel(job_id)
                    self.campaign_job_update(job)
            self.save_campaign(campaign)
            self.advance_campaigns()
            return campaign

    def recover_campaign_cleanup(self, campaign_id):
        with self.lock:
            campaign = self.campaigns[campaign_id]
            if campaign["status"] not in {"interrupted", "failed", "pending"}:
                raise ValueError("Cancel/finish the campaign before recovering cleanup")
            if any(self.jobs[j]["status"] not in JOB_DONE for j in campaign["jobs"]):
                raise ValueError(
                    "Wait for the current runner job to stop before cleanup"
                )
            campaign.update(cancel_requested=True, status="cancelling")
            for case in campaign["cases"]:
                if case["kind"] == "cleanup":
                    case.update(status="waiting", reason=None)
            self.save_campaign(campaign)
            self.advance_campaigns()
            return campaign

    def record_campaign_evidence(
        self, campaign_id, case_id, outcome, reference, note, operator
    ):
        if (
            outcome not in {"manual_pass", "failed"}
            or not str(reference).strip()
            or not str(operator).strip()
        ):
            raise ValueError(
                "Record a manual pass/fail, operator and concrete evidence reference"
            )
        with self.lock:
            campaign = self.campaigns[campaign_id]
            case = next((c for c in campaign["cases"] if c["id"] == case_id), None)
            if not case:
                raise ValueError("Unknown campaign case")
            if case["kind"] != "operator" or case["status"] == "deferred":
                raise ValueError(
                    "Only applicable operator gates accept manual evidence"
                )
            case.update(
                status=outcome,
                operator_evidence={
                    "reference": str(reference)[:2000],
                    "note": str(note)[:4000],
                    "operator": str(operator)[:200],
                    "recorded_at": stamp(),
                },
            )
            if all(c["status"] in DONE for c in campaign["cases"]):
                campaign["status"] = (
                    "interrupted"
                    if campaign["cancel_requested"]
                    else "passed"
                    if all(
                        c["status"] in {"passed", "manual_pass", "deferred"}
                        for c in campaign["cases"]
                    )
                    else "failed"
                    if any(c["status"] == "failed" for c in campaign["cases"])
                    else "pending"
                )
            self.save_campaign(campaign)
            return campaign
