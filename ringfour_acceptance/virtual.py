"""Fixed wrappers for operator-requested virtual launchers; never called by tests."""

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from uuid import UUID

from .diagnostics import scrub
from .model import now

ACTIONS = {"build", "start", "daemon", "stop", "delete", "status", "logs"}


def metadata(root):
    root = Path(root).resolve()
    return {
        "root": str(root),
        "image_workflow": 1,
        "available": all(
            (root / name).is_file() for name in ("up.sh", "upn.sh", "down.sh")
        ),
    }


def normalize(data):
    allowed = {"action", "topology", "start_index", "count", "allow_dirty"}
    if set(data) - allowed:
        raise ValueError("Unsupported launcher option")
    action, topology = data.get("action", "status"), data.get("topology", "singleton")
    if action not in ACTIONS or topology not in {"singleton", "numbered"}:
        raise ValueError("Choose a supported launcher action and topology")
    start, count, dirty = (
        data.get("start_index", 2),
        data.get("count", 1),
        data.get("allow_dirty", False),
    )
    if (
        type(start) is not int
        or type(count) is not int
        or not 1 <= start <= 40
        or not 1 <= count <= 40
        or start + count - 1 > 40
    ):
        raise ValueError("Numbered instances must fit within 1 through 40")
    if type(dirty) is not bool:
        raise ValueError("allow_dirty must be a boolean")
    if count != 1 and (topology != "numbered" or action != "start"):
        raise ValueError("Only numbered Start can select multiple instances")
    return {
        "action": action,
        "topology": topology,
        "start_index": start if topology == "numbered" else 1,
        "count": count,
        "allow_dirty": dirty if action in {"build", "start", "daemon"} else False,
    }


def local_ports(root):
    values = {}
    file = root / ".env"
    if file.exists():
        for line in file.read_text().splitlines():
            key, separator, value = line.strip().partition("=")
            key = key.removeprefix("export ").strip()
            if separator and key in {
                "RINGFOUR_AGENT_HOST_PORT",
                "RINGFOUR_DAEMON_HOST_PORT",
                "RINGFOUR_STATUS_HOST_PORT",
                "RINGFOUR_DEVELOPMENT_HOST_PORT",
            }:
                value = value.split("#", 1)[0].strip().strip("\"'")
                if not value.isdecimal() or not 1 <= int(value) <= 65535:
                    raise ValueError(
                        f"Set a numeric host port for {key} in the virtual checkout .env"
                    )
                values[key] = int(value)
    return values


def plan(root, data):
    root, spec = Path(root).resolve(), normalize(data)
    projects, ports, instances = [], [], []
    overrides = local_ports(root) if spec["topology"] == "singleton" else {}
    for index in range(spec["start_index"], spec["start_index"] + spec["count"]):
        singleton = spec["topology"] == "singleton"
        offset = 0 if singleton else (index - 1) * 10
        project = "ringfour-virtual" if singleton else f"ringfour-virtual-{index}"
        simulation = None if singleton else f"ringfour-sim-{index}"
        projects += [project] + ([simulation] if simulation else [])
        selected_ports = {
            "agent": overrides.get("RINGFOUR_AGENT_HOST_PORT", 10101 + offset),
            "daemon": overrides.get("RINGFOUR_DAEMON_HOST_PORT", 8081 + offset),
            "status": overrides.get("RINGFOUR_STATUS_HOST_PORT", 8083 + offset),
            "development": overrides.get("RINGFOUR_DEVELOPMENT_HOST_PORT", 8443 + offset),
        }
        ports += [
            {"port": p, "role": role, "project": project}
            for role, p in selected_ports.items()
        ]
        instances.append(
            {
                "index": index,
                "project": project,
                "simulator_project": simulation,
                "ros_domain": 186 + index,
                "ports": selected_ports,
            }
        )
    action = spec["action"]
    argv, environment = [], {}
    if spec["topology"] == "singleton" and action in {
        "start",
        "daemon",
        "stop",
        "delete",
    }:
        environment["COMPOSE_PROJECT_NAME"] = "ringfour-virtual"
    if action == "build":
        argv = [str(root / "build.sh")]
        if spec["allow_dirty"]:
            environment["RINGFOUR_ALLOW_DIRTY_APP_SOURCES"] = "true"
    elif action in {"start", "daemon"}:
        argv = (
            [str(root / "up.sh"), "-d"]
            if spec["topology"] == "singleton"
            else [
                str(root / "upn.sh"),
                str(spec["count"]),
                "--start-index",
                str(spec["start_index"]),
            ]
        )
        argv += ["--no-build", "--force-recreate"]
        if action == "daemon":
            argv += ["--no-deps", "daemon"]
        if spec["allow_dirty"]:
            environment["RINGFOUR_ALLOW_DIRTY_APP_SOURCES"] = "true"
    elif action in {"stop", "delete"}:
        argv = [str(root / "down.sh")]
        if spec["topology"] == "numbered":
            argv += ["--instance", str(spec["start_index"])]
    else:
        argv = [
            "python3",
            "-m",
            "ringfour_acceptance.virtual",
            "--root",
            str(root),
            "--action",
            action,
            "--topology",
            spec["topology"],
            "--start-index",
            str(spec["start_index"]),
        ]
    # Ignore unrelated caller environment. Launchers load their own checkout .env;
    # worker execution below removes inherited topology overrides first.
    result = {
        "spec": spec,
        "root": str(root),
        "argv": argv,
        "environment": environment,
        "projects": projects,
        "instances": instances,
        "ports": ports,
        "stack_operations": action in {"build", "start", "daemon", "stop", "delete"},
    }
    result["fingerprint"] = hashlib.sha256(
        json.dumps(result, sort_keys=True).encode()
    ).hexdigest()
    prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in environment.items())
    result["command"] = (prefix + " " if prefix else "") + shlex.join(argv)
    return result


def emit(message):
    print(f"[{now()}] {scrub(message)}", flush=True)


def docker_rows():
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Docker is unavailable for this worker: " + scrub(result.stderr[-1500:])
        )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def project_of(row):
    labels = dict(
        item.split("=", 1) for item in row.get("Labels", "").split(",") if "=" in item
    )
    return labels.get("com.docker.compose.project")


def selected_rows(rows, selected):
    return [r for r in rows if project_of(r) in selected["projects"]]


def check_ports(rows, selected):
    for row in rows:
        if row.get("State") != "running":
            continue
        used = {int(p) for p in re.findall(r":(\d+)->", row.get("Ports", ""))}
        for item in selected["ports"]:
            if selected["spec"]["action"] == "daemon" and item["role"] == "agent":
                continue
            if item["port"] in used and project_of(row) != item["project"]:
                raise RuntimeError(
                    f"Host port {item['port']} for {item['project']} is occupied by {row.get('Names')}. No launcher was started."
                )


def execute(root, data, output, fingerprint):
    output.mkdir(parents=True, exist_ok=True)
    selected = plan(root, data)
    if selected["fingerprint"] != fingerprint:
        raise ValueError("Launcher plan changed; preview it again before running")
    report = {
        "passed": False,
        "stage": "preflight",
        "started_at": now(),
        "plan": selected,
        "stack_operations": False,
        "containers": [],
    }
    path = output / "virtual-operation.json"

    def save():
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(report, indent=2) + "\n")
        pending.replace(path)

    save()
    emit(f"Virtual {selected['spec']['action']} · {', '.join(selected['projects'])}")
    emit("Command: " + selected["command"])
    try:
        rows = docker_rows()
        if selected["spec"]["action"] == "delete" and not selected_rows(rows, selected):
            emit("No stack containers exist; removing the saved stack card only.")
            report.update(passed=True, stage="completed")
            return
        if selected["spec"]["action"] in {"start", "daemon"}:
            check_ports(rows, selected)
        if not selected["stack_operations"]:
            report["stage"] = "observing"
            log_failures = []
            for index, row in enumerate(selected_rows(rows, selected)):
                item = {
                    key: row.get(key)
                    for key in ("ID", "Names", "State", "Status", "Ports", "Image")
                }
                labels = dict(
                    part.split("=", 1)
                    for part in row.get("Labels", "").split(",")
                    if "=" in part
                )
                item["service"] = labels.get("com.docker.compose.service", "container")
                state = subprocess.run(
                    ["docker", "inspect", "--format", "{{json .State}}", row["ID"]],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                if state.returncode:
                    item["inspection_error"] = "Container changed during observation"
                else:
                    item["error"] = json.loads(state.stdout).get("Error", "")
                report["containers"].append(item)
                emit(json.dumps(item))
                if selected["spec"]["action"] == "logs":
                    logs = subprocess.run(
                        ["docker", "logs", "--tail", "120", "--timestamps", row["ID"]],
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    captured = {}
                    for stream, content in (
                        ("stdout", logs.stdout),
                        ("stderr", logs.stderr),
                    ):
                        filename = f"container-{index}-{stream}.log"
                        (output / filename).write_text(scrub(content))
                        captured[stream] = filename
                    item["logs"] = captured | {"returncode": logs.returncode}
                    if logs.returncode:
                        log_failures.append(row["Names"])
                    emit(
                        f"Captured {row['Names']} · stdout/stderr saved separately · exit {logs.returncode}"
                    )
            if not report["containers"]:
                emit("No containers found for the selected Compose projects.")
            if log_failures:
                raise RuntimeError(
                    "Could not read logs for "
                    + ", ".join(log_failures)
                    + "; see container stderr"
                )
            report["passed"] = True
        else:
            if not metadata(root)["available"]:
                raise ValueError(
                    "Virtual launcher scripts are missing from the worker checkout"
                )
            report.update(stage="launcher", stack_operations=True)
            save()
            env = {
                k: v
                for k, v in os.environ.items()
                if not (
                    k.startswith(("COMPOSE_", "RINGFOUR_"))
                    or k == "BASE101_NETWORK_NAME"
                    or k == "PLATFORM_API_URL"
                )
            }
            env.update(selected["environment"])
            socket = Path("/var/run/docker.sock")
            if socket.exists():
                env["RINGFOUR_ENGINE_SOCKET_GID"] = str(socket.stat().st_gid)
            raw = output / "launcher.log"
            with raw.open("w") as stream, raw.open(errors="replace") as reader:
                process = subprocess.Popen(
                    selected["argv"],
                    cwd=root,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                cancelled, buffered = False, ""
                last_output = time.monotonic()
                while True:
                    try:
                        code = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        code = None
                    except KeyboardInterrupt:
                        cancelled = True
                        emit(
                            "Cancellation requested; waiting for launcher cleanup. Partial startup is not rolled back."
                        )
                        os.killpg(process.pid, signal.SIGINT)
                        continue
                    chunk = reader.read()
                    if chunk:
                        buffered += chunk
                        last_output = time.monotonic()
                    while "\n" in buffered:
                        line, buffered = buffered.split("\n", 1)
                        emit(line)
                    if code is not None:
                        if buffered:
                            emit(buffered)
                        break
                    if time.monotonic() - last_output > 10:
                        emit("Launcher still running; waiting for output")
                        last_output = time.monotonic()
                report.update(
                    returncode=code,
                    passed=code == 0 and not cancelled,
                    interrupted=cancelled,
                )
                if cancelled:
                    raise InterruptedError(
                        "Launcher interrupted; inspect container status before retrying"
                    )
                if code:
                    raise RuntimeError(
                        f"Launcher exited with code {code}; see launcher.log"
                    )
        report["stage"] = "completed"
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        report.update(error=scrub(str(error)), stage="failed", passed=False)
        emit("FAILED · " + (report["error"] or "Interrupted"))
        raise
    finally:
        report["completed_at"] = now()
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--action", required=True, choices=sorted(ACTIONS))
    parser.add_argument(
        "--topology", choices=["singleton", "numbered"], default="singleton"
    )
    parser.add_argument("--start-index", type=int, default=2)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--fingerprint")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    spec = {
        "action": args.action,
        "topology": args.topology,
        "start_index": args.start_index,
        "count": args.count,
        "allow_dirty": args.allow_dirty,
    }
    try:
        selected = plan(args.root, spec)
        execute(
            args.root.resolve(),
            spec,
            args.output or Path(tempfile.mkdtemp(prefix="ringfour-virtual-operation-")),
            args.fingerprint or selected["fingerprint"],
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, scrub(str(error)) + "\n")


def empty_components(topology):
    return {
        name: {
            "state": "external"
            if name == "sim" and topology == "singleton"
            else "not_started",
            "container": None,
        }
        for name in ("runner", "daemon", "sim")
    }


class DeviceIdentityParser(HTMLParser):
    """Read only the public device page's identity, never App output."""

    def __init__(self):
        super().__init__()
        self.title, self.detail = "", ""
        self.in_title, self.in_detail, self.has_detail = False, False, False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "div" and "detail" in dict(attrs).get("class", "").split():
            self.in_detail = self.has_detail = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "div":
            self.in_detail = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_detail:
            self.detail += data

    def identity(self):
        if self.title.strip() != "Ringfour device" or not self.has_detail:
            return {"robot_id": None, "identity_state": "unavailable"}
        if self.detail.strip().startswith("Robot ID:"):
            try:
                return {
                    "robot_id": str(
                        UUID(self.detail.strip().removeprefix("Robot ID:").strip())
                    ),
                    "identity_state": "enrolled",
                }
            except ValueError:
                return {"robot_id": None, "identity_state": "unavailable"}
        return {"robot_id": None, "identity_state": "not_enrolled"}


def host_port(component, target):
    match = re.search(
        r"(?:127\.0\.0\.1|0\.0\.0\.0|\[::\]):(\d+)->" + str(target) + r"/tcp",
        component.get("ports", ""),
    )
    return int(match[1]) if match and 1 <= int(match[1]) <= 65535 else None


def daemon_identity(component):
    unavailable = {"robot_id": None, "identity_state": "unavailable"}
    if component.get("state") != "running":
        return unavailable
    port = host_port(component, 8083)
    if port:
        connection = HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            if response.status == 200:
                parser = DeviceIdentityParser()
                parser.feed(response.read(16384).decode("utf-8", errors="replace"))
                identity = parser.identity()
                if identity["identity_state"] != "unavailable":
                    return identity
        except (OSError, HTTPException, ValueError):
            pass
        finally:
            connection.close()
    # WSL host port forwarding can be unavailable even for a running Daemon.
    # Read the same public status page inside that exact container; no DB or
    # credential files are accessed and page output is never logged/persisted.
    container_id = component.get("container_id") or ""
    if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        return unavailable
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "python",
                "-c",
                "import sys,urllib.request; sys.stdout.buffer.write(urllib.request.urlopen('http://127.0.0.1:8083/', timeout=1).read(16384))",
            ],
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            parser = DeviceIdentityParser()
            parser.feed(result.stdout.decode("utf-8", errors="replace"))
            return parser.identity()
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return unavailable


def inventory(root):
    """Read component state; never starts a container or creates a stack."""
    result = metadata(root) | {"observed_at": now()}
    if not result["available"]:
        return result
    try:
        rows = docker_rows()
        discovered = []
        projects = {project_of(row) for row in rows}
        # A failed launch can leave only the simulator. Keep that partial stack
        # visible, while leaving the legacy singleton's external simulator alone.
        for project in list(projects):
            match = re.fullmatch(r"ringfour-sim-([1-9][0-9]*)", project or "")
            if match and (int(match[1]) > 1 or "ringfour-virtual" not in projects):
                projects.add("ringfour-virtual-" + match[1])
        for project in sorted(
            p
            for p in projects
            if p and re.fullmatch(r"ringfour-virtual(?:-[1-9][0-9]*)?", p)
        ):
            topology = "singleton" if project == "ringfour-virtual" else "numbered"
            index = 1 if topology == "singleton" else int(project.rsplit("-", 1)[1])
            if not 1 <= index <= 40:
                continue
            components = empty_components(topology)
            for row in rows:
                labels = dict(
                    item.split("=", 1)
                    for item in row.get("Labels", "").split(",")
                    if "=" in item
                )
                service = labels.get("com.docker.compose.service")
                role = None
                if project_of(row) == project and service in {"agent", "daemon"}:
                    role = "runner" if service == "agent" else "daemon"
                elif (
                    topology == "numbered"
                    and project_of(row) == f"ringfour-sim-{index}"
                    and service == "simulator"
                ):
                    role = "sim"
                if role:
                    components[role] = {
                        "state": row.get("State", "unknown"),
                        "container": row.get("Names"),
                        "container_id": row.get("ID"),
                        "ports": row.get("Ports", ""),
                        "status": row.get("Status", ""),
                    }
            ports = plan(
                root, {"action": "status", "topology": topology, "start_index": index}
            )["instances"][0]["ports"]
            for name, role, target in (
                ("agent", "runner", 10101),
                ("daemon", "daemon", 8081),
                ("status", "daemon", 8083),
            ):
                actual = host_port(components[role], target)
                if actual:
                    ports[name] = actual
            discovered.append(
                {
                    "topology": topology,
                    "index": index,
                    "components": components,
                    "ports": ports,
                    **daemon_identity(components["daemon"]),
                }
            )
        result["stacks"] = discovered
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result["inventory_error"] = scrub(str(error))[:1000]
    return result


if __name__ == "__main__":
    main()
