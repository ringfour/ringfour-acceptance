"""Durable jobs and ephemeral worker sessions; no platform credentials stored."""

import json
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .compliance import ComplianceStore

PROFILES = {"quick", "integration", "full", "preflight", "live"}
TERMINAL = {"passed", "failed", "pending", "interrupted"}


def now():
    return datetime.now(UTC).isoformat()


def target_valid(identity, target):
    if target.get("user_id") != (identity.get("user") or {}).get("id"):
        raise ValueError(
            "Selected user does not match this worker's authenticated identity"
        )
    org = next(
        (
            o
            for o in identity.get("organizations", [])
            if o["id"] == target.get("org_id")
        ),
        None,
    )
    if not org or not any(r["id"] == target.get("robot_id") for r in org["robots"]):
        raise ValueError("Select an organization and robot visible to this user")
    if identity.get("selected_org") != target["org_id"]:
        raise ValueError(
            "Select this organization with ringfour-cli org select in the worker shell, then refresh"
        )


class Store(ComplianceStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.workers, self.pairings, self.jobs = {}, {}, {}
        self.stacks = {}
        self.deleted_stacks = {}
        if (self.root / "deleted-stacks.json").exists():
            self.deleted_stacks = json.loads(
                (self.root / "deleted-stacks.json").read_text()
            )
        if (self.root / "stacks.json").exists():
            self.stacks = json.loads((self.root / "stacks.json").read_text())
        self.setup = {"repository": "127.0.0.1:5000/ringfour/acceptance", "targets": []}
        if (self.root / "setup.json").exists():
            self.setup = json.loads((self.root / "setup.json").read_text())
        for file in self.root.glob("runs/*/job.json"):
            job = json.loads(file.read_text())
            if job["status"] not in TERMINAL:
                job.update(
                    status="interrupted",
                    reason="Control room restarted. Review worker cleanup before retrying.",
                    finished_at=now(),
                )
                self.save(job)
            self.jobs[job["id"]] = job

        self.init_compliance()

    def save(self, job):
        folder = self.root / "runs" / job["id"]
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "job.json.tmp"
        temporary.write_text(json.dumps(job, indent=2) + "\n")
        temporary.replace(folder / "job.json")

    def pairing(self):
        with self.lock:
            code = secrets.token_hex(12)
            self.pairings = {k: v for k, v in self.pairings.items() if v > time.time()}
            self.pairings[code] = time.time() + 300
            return code

    def register(self, code, label, identity, launchers=None, build=None):
        with self.lock:
            if self.pairings.pop(code, 0) < time.time():
                raise ValueError(
                    "Pairing code expired or already used; create another in Setup"
                )
            worker_id, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
            self.workers[worker_id] = {
                "id": worker_id,
                "label": str(label)[:100],
                "identity": identity,
                "launchers": launchers or {},
                "build": build or {},
                "token": token,
                "last_seen": time.time(),
            }
            self.rebind_targets(self.workers[worker_id])
            self.sync_stacks(self.workers[worker_id])
            return {"worker_id": worker_id, "token": token}

    def rebind_targets(self, worker):
        """Restore saved targets only after the same discovered identity reconnects."""
        with self.lock:
            changed = False
            for target in self.setup["targets"]:
                if target["worker_id"] in self.workers:
                    continue
                try:
                    target_valid(worker["identity"], target)
                except ValueError:
                    continue
                target["worker_id"] = worker["id"]
                changed = True
            if changed:
                pending = self.root / "setup.json.tmp"
                pending.write_text(json.dumps(self.setup, indent=2) + "\n")
                pending.replace(self.root / "setup.json")

    def authenticate(self, token):
        with self.lock:
            for worker in self.workers.values():
                if secrets.compare_digest(worker["token"], token):
                    worker["last_seen"] = time.time()
                    return worker
        raise PermissionError("Worker authentication required")

    def public(self):
        with self.lock:
            return {
                **self.compliance_public(),
                "setup": self.setup,
                "stacks": sorted(
                    self.stacks.values(),
                    key=lambda stack: (
                        stack["root"],
                        stack["index"],
                        stack["topology"],
                    ),
                ),
                "workers": [
                    {k: v for k, v in w.items() if k != "token"}
                    | {"online": time.time() - w["last_seen"] < 15}
                    for w in self.workers.values()
                ],
                "jobs": sorted(
                    self.jobs.values(), key=lambda job: job["created_at"], reverse=True
                ),
            }

    def configure(self, setup):
        targets = setup.get("targets", [])
        if not isinstance(targets, list) or len(targets) > 20:
            raise ValueError("Choose up to 20 targets")
        cleaned = []
        for target in targets:
            worker = self.workers.get(target.get("worker_id"))
            if not worker:
                raise ValueError("Connect a worker for every selected user")
            target_valid(worker["identity"], target)
            cleaned.append(
                {k: target[k] for k in ("worker_id", "user_id", "org_id", "robot_id")}
            )
        repository = str(setup.get("repository", "")).strip()
        if (
            len(repository) > 500
            or any(c.isspace() for c in repository)
            or repository.startswith("-")
        ):
            raise ValueError("Enter a registry repository without spaces")
        with self.lock:
            self.setup = {"repository": repository, "targets": cleaned}
            temp = self.root / "setup.json.tmp"
            temp.write_text(json.dumps(self.setup, indent=2) + "\n")
            temp.replace(self.root / "setup.json")
        return self.setup

    def enqueue(self, profile, worker_id=None):
        if profile not in PROFILES:
            raise ValueError("Unknown acceptance profile")
        with self.lock:
            targets = (
                self.setup["targets"] if profile in {"preflight", "live"} else [None]
            )
            if not targets:
                raise ValueError("Save at least one live target in Setup first")
            if profile == "live" and not self.setup["repository"]:
                raise ValueError("A registry repository is required for live workflows")
            specs = []
            for target in targets:
                worker = self.workers.get(target["worker_id"] if target else worker_id)
                if not worker or time.time() - worker["last_seen"] >= 15:
                    raise ValueError("The selected worker is offline")
                if target:
                    target_valid(worker["identity"], target)
                # Reserve each worker; no parallel fixture/live suites per account.
                if any(
                    j["worker_id"] == worker["id"] and j["status"] not in TERMINAL
                    for j in self.jobs.values()
                ):
                    raise ValueError("Worker already has a queued or running job")
                if any(s[0]["id"] == worker["id"] for s in specs):
                    raise ValueError("Choose only one target per worker for a sweep")
                specs.append((worker, target))
            created = []
            group = str(uuid.uuid4())
            for worker, target in specs:
                job = {
                    "id": str(uuid.uuid4()),
                    "group_id": group,
                    "profile": profile,
                    "worker_id": worker["id"],
                    "worker_label": worker["label"],
                    "target": target,
                    "repository": self.setup["repository"],
                    "status": "queued",
                    "created_at": now(),
                    "cancel_requested": False,
                    "progress": {},
                    "log": "",
                    "files": {},
                }
                self.jobs[job["id"]] = job
                self.save(job)
                created.append(job)
            return created

    def save_stacks(self):
        pending = self.root / "stacks.json.tmp"
        pending.write_text(json.dumps(self.stacks, indent=2) + "\n")
        pending.replace(self.root / "stacks.json")

    def save_deleted_stacks(self):
        pending = self.root / "deleted-stacks.json.tmp"
        pending.write_text(json.dumps(self.deleted_stacks, indent=2) + "\n")
        pending.replace(self.root / "deleted-stacks.json")

    def sync_stacks(self, worker):
        from .virtual import empty_components, plan

        launchers = worker.get("launchers") or {}
        if "stacks" not in launchers or launchers.get("inventory_error"):
            return
        with self.lock:
            root = launchers["root"]
            changed = False
            observed = {
                (item["topology"], item["index"]): item for item in launchers["stacks"]
            }
            for (topology, index), item in observed.items():
                stack_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{root}:{topology}:{index}")
                )
                deleted_at = self.deleted_stacks.get(stack_id)
                if deleted_at and (launchers.get("observed_at") or "") <= deleted_at:
                    continue
                if stack_id not in self.stacks:
                    self.stacks[stack_id] = {
                        "id": stack_id,
                        "name": f"Stack {index}",
                        "root": root,
                        "topology": topology,
                        "index": index,
                        "worker_id": worker["id"],
                        "allow_dirty": True,
                        "created_at": now(),
                    }
                    changed = True
            for stack in self.stacks.values():
                if stack["root"] != root:
                    continue
                previous = self.workers.get(stack["worker_id"])
                if (
                    stack["worker_id"] != worker["id"]
                    and previous
                    and time.time() - previous["last_seen"] < 15
                ):
                    continue
                before = json.dumps(stack, sort_keys=True)
                item = observed.get((stack["topology"], stack["index"]))
                stack.update(
                    worker_id=worker["id"],
                    observed_at=launchers.get("observed_at"),
                    components=item["components"]
                    if item
                    else empty_components(stack["topology"]),
                )
                stack["ports"] = (
                    item.get("ports")
                    if item and item.get("ports")
                    else plan(
                        root,
                        {
                            "action": "status",
                            "topology": stack["topology"],
                            "start_index": stack["index"],
                        },
                    )["instances"][0]["ports"]
                )
                identity_state = (
                    item.get("identity_state", "unavailable") if item else "unavailable"
                )
                if identity_state == "enrolled":
                    stack["robot_id"] = item["robot_id"]
                elif identity_state == "not_enrolled":
                    stack["robot_id"] = None
                stack["identity_state"] = identity_state
                changed = changed or before != json.dumps(stack, sort_keys=True)
            if changed:
                self.save_stacks()

    def add_stack(self, worker_id):
        from .virtual import empty_components, plan

        with self.lock:
            # Validate worker/check-out capability without launching anything.
            selected = self.virtual_plan(worker_id, {"action": "status"})
            root = selected["root"]
            used = {s["index"] for s in self.stacks.values() if s["root"] == root}
            index = next((i for i in range(2, 41) if i not in used), None)
            if index is None:
                raise ValueError("All numbered stack slots are allocated")
            stack_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{root}:numbered:{index}"))
            stack = {
                "id": stack_id,
                "name": f"Stack {index}",
                "root": root,
                "topology": "numbered",
                "index": index,
                "worker_id": worker_id,
                "allow_dirty": True,
                "components": empty_components("numbered"),
                "ports": plan(
                    root,
                    {"action": "status", "topology": "numbered", "start_index": index},
                )["instances"][0]["ports"],
                "robot_id": None,
                "identity_state": "unavailable",
                "created_at": now(),
            }
            self.stacks[stack_id] = stack
            self.deleted_stacks.pop(stack_id, None)
            self.save_stacks()
            self.save_deleted_stacks()
            return stack

    def configure_stack(self, stack_id, worker_id, allow_dirty):
        with self.lock:
            if type(allow_dirty) is not bool:
                raise ValueError("allow_dirty must be a boolean")
            stack = self.stacks[stack_id]
            selected = self.virtual_plan(worker_id, {"action": "status"})
            if selected["root"] != stack["root"]:
                raise ValueError("Choose a worker using this stack's checkout")
            if any(
                j.get("stack_id") == stack_id and j["status"] not in TERMINAL
                for j in self.jobs.values()
            ):
                raise ValueError("Wait for this stack's current operation to finish")
            stack.update(worker_id=worker_id, allow_dirty=allow_dirty)
            self.save_stacks()
            return stack

    def stack_action(self, stack_id, action):
        with self.lock:
            stack = self.stacks[stack_id]
            spec = {
                "action": action,
                "topology": stack["topology"],
                "start_index": stack["index"],
                "count": 1,
                "allow_dirty": stack["allow_dirty"],
            }
            selected = self.virtual_plan(stack["worker_id"], spec)
            job = self.enqueue_virtual(
                stack["worker_id"], selected["spec"], selected["fingerprint"]
            )
            job.update(
                stack_id=stack_id,
                stack_name=stack["name"],
                delete_stack=action == "delete",
            )
            self.save(job)
            self.campaign_job_update(job)
            return job

    def virtual_plan(self, worker_id, spec):
        from .virtual import plan

        worker = self.workers.get(worker_id)
        if not worker or time.time() - worker["last_seen"] >= 15:
            raise ValueError("Connect an online worker first")
        launchers = worker.get("launchers") or {}
        if not launchers.get("available") or not launchers.get("root"):
            raise ValueError(
                "This worker has no virtual launchers; reconnect an updated worker with --virtual"
            )
        return plan(launchers["root"], spec)

    def enqueue_virtual(self, worker_id, spec, fingerprint):
        with self.lock:
            selected = self.virtual_plan(worker_id, spec)
            if selected["fingerprint"] != fingerprint:
                raise ValueError("Launcher plan changed; preview it again")
            if any(
                j["worker_id"] == worker_id and j["status"] not in TERMINAL
                for j in self.jobs.values()
            ):
                raise ValueError("Worker already has a queued or running job")
            worker = self.workers[worker_id]
            job = {
                "id": str(uuid.uuid4()),
                "group_id": str(uuid.uuid4()),
                "profile": "virtual",
                "worker_id": worker_id,
                "worker_label": worker["label"],
                "target": None,
                "launcher": selected,
                "status": "queued",
                "created_at": now(),
                "cancel_requested": False,
                "progress": {},
                "log": "",
                "files": {},
            }
            self.jobs[job["id"]] = job
            self.save(job)
            self.campaign_job_update(job)
            return job

    def claim(self, worker):
        with self.lock:
            self.rebind_campaign_jobs(worker)
            self.advance_campaigns()
            # Globally sequential: process fixtures can share engine/image names.
            if any(j["status"] in {"running", "stopping"} for j in self.jobs.values()):
                return None
            for job in self.jobs.values():
                if job["worker_id"] == worker["id"] and job["status"] == "queued":
                    if job["profile"] == "virtual" and not worker.get(
                        "launchers", {}
                    ).get("available"):
                        continue
                    job.update(status="running", started_at=now())
                    self.save(job)
                    return job
            return None

    def update(self, worker, job_id, payload):
        with self.lock:
            job = self.jobs[job_id]
            if job["worker_id"] != worker["id"]:
                raise PermissionError("Job belongs to another worker")
            if job["status"] not in {"running", "stopping"}:
                raise ValueError("Job is no longer active")
            for key in ("progress", "log", "files", "reason", "returncode"):
                if key in payload:
                    job[key] = payload[key]
            state = payload.get("status")
            if state:
                if state not in TERMINAL:
                    raise ValueError("Invalid final job state")
                job.update(status=state, finished_at=now())
                if state == "passed" and job.get("delete_stack"):
                    operation = job["progress"].get("virtual-operation") or {}
                    if (
                        operation.get("passed") is not True
                        or operation.get("stage") != "completed"
                    ):
                        job.update(
                            status="failed",
                            reason="Stack removal was not confirmed; the card was retained",
                        )
                    else:
                        self.stacks.pop(job["stack_id"], None)
                        self.deleted_stacks[job["stack_id"]] = now()
                        self.save_stacks()
                        self.save_deleted_stacks()
            self.save(job)
            self.campaign_job_update(job)
            self.advance_campaigns()
            return {"cancel": job["cancel_requested"]}

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] == "queued":
                job.update(status="interrupted", finished_at=now())
            elif job["status"] not in TERMINAL:
                job.update(cancel_requested=True, status="stopping")
            self.save(job)
            self.campaign_job_update(job)
            return job
