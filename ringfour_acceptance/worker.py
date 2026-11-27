"""One worker per OS account. Executes the Platform command-line adapter."""

import getpass
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .builds import snapshot as build_snapshot
from .diagnostics import scrub
from .model import target_valid
from .virtual import inventory, plan


def request(server, path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(payload).encode()
    try:
        with urlopen(
            Request(server + path, data=body, headers=headers), timeout=15
        ) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.loads(error.read(8192)).get("error")
            if isinstance(detail, str):
                error.msg = scrub(detail[:1000])
        except (ValueError, AttributeError):
            pass
        finally:
            error.close()
        raise


def discover(platform):
    interpreter = platform / "apps/cli/.venv/bin/python"
    if not interpreter.exists():
        return {
            "error": "Prepare the Platform CLI environment with uv sync --locked in apps/cli."
        }
    try:
        result = subprocess.run(
            [str(interpreter), str(Path(__file__).with_name("probe.py"))],
            cwd=platform,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
        )
        return (
            json.loads(result.stdout)
            if result.returncode == 0
            else {"error": "CLI identity discovery failed"}
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {
            "error": "CLI identity discovery unavailable. Check login and API connectivity."
        }


def command(platform, job, output, virtual_root=None):
    if job["profile"] == "compliance":
        if job["case_kind"] == "regression":
            return [
                sys.executable,
                str(platform / "scripts/apps-acceptance.py"),
                "--suite",
                job["action"],
                "--output",
                str(output),
            ]
        payload = dict(job) | {"platform_root": str(platform)}
        file = output / "case-input.json"
        file.write_text(json.dumps(payload))
        return [
            str(platform / "apps/cli/.venv/bin/python"),
            str(Path(__file__).with_name("live_cases.py")),
            "--input",
            str(file),
            "--output",
            str(output),
        ]
    if job["profile"] == "virtual":
        selected = plan(
            virtual_root or platform.parent / "ringfour-virtual",
            job["launcher"]["spec"],
        )
        if selected["fingerprint"] != job["launcher"]["fingerprint"]:
            raise ValueError("Launcher plan changed; preview again before running")
        spec = selected["spec"]
        args = [
            sys.executable,
            "-m",
            "ringfour_acceptance.virtual",
            "--root",
            selected["root"],
            "--action",
            spec["action"],
            "--topology",
            spec["topology"],
            "--start-index",
            str(spec["start_index"]),
            "--count",
            str(spec["count"]),
            "--fingerprint",
            selected["fingerprint"],
            "--output",
            str(output),
        ]
        if spec["allow_dirty"]:
            args += ["--allow-dirty"]
        return args

    args = [
        sys.executable,
        str(platform / "scripts/apps-acceptance.py"),
        "--profile",
        "live" if job["profile"] == "preflight" else job["profile"],
        "--output",
        str(output),
    ]
    if job["profile"] in {"preflight", "live"}:
        target = job["target"]
        args += ["--live-org", target["org_id"], "--live-robot", target["robot_id"]]
        if job["profile"] == "preflight":
            args += ["--live-preflight"]
        else:
            args += ["--live-repository", job["repository"]]
    return args


def snapshot(output, log):
    files, progress = {}, {}
    for path in sorted(output.glob("*")):
        if path.is_file() and path.suffix in {".json", ".md", ".log"}:
            # Bound live polling payloads. Originals remain on the worker disk.
            content = scrub(path.read_text(errors="replace")[-160000:])
            files[path.name] = content
            if path.name in {
                "report.json",
                "live-workflow.json",
                "live-imu-settings.json",
                "virtual-operation.json",
                "case-report.json",
            }:
                try:
                    progress[path.stem] = json.loads(content)
                except ValueError:
                    pass  # Atomicity is owned by the original runner.
    return {
        "progress": progress,
        "files": files,
        "log": scrub(log.read_text(errors="replace")[-80000:]),
    }


def execute(server, token, platform, job, stop, virtual_root=None):
    # Workers own writable evidence folders; ring1 never writes into cdr's checkout.
    output = Path(tempfile.mkdtemp(prefix="ringfour-acceptance-"))
    log = output / "runner.log"
    process, sent_interrupt = None, False
    try:
        if job["profile"] == "compliance":
            build = build_snapshot(platform)
            if (
                build["fingerprint"] != job["expected_build"]
                and job["case_kind"] != "cleanup"
            ):
                raise ValueError(
                    "Runner build changed after campaign scheduling; start a new campaign"
                )
            (output / "build-identity.json").write_text(json.dumps(build, indent=2))
        if job["profile"] in {"preflight", "live"}:
            target_valid(discover(platform), job["target"])
        with log.open("w") as stream:
            process = subprocess.Popen(
                command(platform, job, output, virtual_root),
                cwd=Path(__file__).resolve().parents[1]
                if job["profile"] == "virtual"
                else platform,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=os.environ
                | {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            )
            while process.poll() is None:
                try:
                    answer = request(
                        server,
                        "/worker/update",
                        {"job_id": job["id"], **snapshot(output, log)},
                        token,
                    )
                    cancel = answer["cancel"]
                except (OSError, ValueError):
                    # Losing the control room must never leave a job silently unattended.
                    cancel = True
                if (cancel or stop.is_set()) and not sent_interrupt:
                    # Interrupt only the parent runner. Its own handler coordinates child cleanup.
                    process.send_signal(signal.SIGINT)
                    sent_interrupt = True
                time.sleep(1)
        result = snapshot(output, log)
        result.update(
            status="interrupted"
            if sent_interrupt
            else {0: "passed", 2: "pending", 78: "pending", 130: "interrupted"}.get(
                process.returncode, "failed"
            ),
            returncode=process.returncode,
            reason=f"Worker evidence: {output}",
        )
        request(server, "/worker/update", {"job_id": job["id"], **result}, token)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            process.wait()  # Do not hard-kill a live cleanup.
        try:
            request(
                server,
                "/worker/update",
                {
                    "job_id": job["id"],
                    "status": "failed",
                    "reason": f"{type(error).__name__}: {scrub(str(error))[:500]} · evidence: {output}",
                },
                token,
            )
        except (OSError, ValueError):
            print(
                f"Control room unavailable; inspect worker evidence at {output}",
                flush=True,
            )


def run(server, platform, pairing, label=None, stop=None, virtual_root=None):
    parsed = urlparse(server)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("Workers connect to a loopback control room only")
    platform = Path(platform).resolve()
    if not (platform / "scripts/apps-acceptance.py").is_file():
        raise ValueError("Platform checkout must contain scripts/apps-acceptance.py")
    stop = stop or threading.Event()
    virtual_root = Path(virtual_root or platform.parent / "ringfour-virtual").resolve()
    launchers = inventory(virtual_root)
    identity = discover(platform)
    build = build_snapshot(platform)
    registration = request(
        server,
        "/worker/register",
        {
            "pairing": pairing,
            "label": label or getpass.getuser(),
            "identity": identity,
            "launchers": launchers,
            "build": build,
        },
    )
    token = registration["token"]
    print(
        f"Worker connected as {label or getpass.getuser()}; credentials stay in this account.",
        flush=True,
    )
    refreshed = virtual_refreshed = time.monotonic()
    while not stop.is_set():
        try:
            if time.monotonic() - refreshed > 30:
                identity, refreshed = discover(platform), time.monotonic()
                build = build_snapshot(platform)
            if time.monotonic() - virtual_refreshed >= 10:
                launchers, virtual_refreshed = inventory(virtual_root), time.monotonic()
            answer = request(
                server,
                "/worker/poll",
                {"identity": identity, "launchers": launchers, "build": build},
                token,
            )
            if answer.get("job"):
                execute(server, token, platform, answer["job"], stop, virtual_root)
                identity, refreshed = discover(platform), time.monotonic()
                build = build_snapshot(platform)
                virtual_refreshed = 0
            else:
                stop.wait(2)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            print(
                f"Worker disconnected ({type(error).__name__}); pair again if the control room restarted.",
                flush=True,
            )
            return
