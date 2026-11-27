"""Loopback HTTP UI and authenticated worker protocol, using Python stdlib."""

import json
import mimetypes
import secrets
import shlex
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .model import Store

STATIC = Path(__file__).with_name("static")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, state, platform=None, virtual_root=None):
        self.platform = Path(platform or "../ringfour-platform").resolve()
        self.virtual_root = Path(
            virtual_root or self.platform.parent / "ringfour-virtual"
        ).resolve()
        self.store = Store(state)
        self.browser_token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, data, status=200, content_type="application/json", cookie=False):
        body = json.dumps(data).encode() if content_type == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; font-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        if cookie:
            self.send_header(
                "Set-Cookie",
                f"rf_acceptance={self.server.browser_token}; HttpOnly; SameSite=Strict; Path=/",
            )
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") in {
            f"localhost:{self.server.server_port}",
            f"127.0.0.1:{self.server.server_port}",
        }

    def browser(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("rf_acceptance")
        if not token or not secrets.compare_digest(
            token.value, self.server.browser_token
        ):
            raise PermissionError("Open the control room in this browser first")

    def worker(self):
        return self.server.store.authenticate(
            self.headers.get("Authorization", "").removeprefix("Bearer ")
        )

    def do_GET(self):
        if not self.valid_host():
            self.reply({"error": "Loopback Host required"}, 403)
            return
        try:
            path = urlparse(self.path).path
            if path == "/api/state":
                self.browser()
                self.reply(self.server.store.public())
                return
            if path.startswith("/api/campaigns/"):
                self.browser()
                parts = path.split("/")
                campaign = self.server.store.campaigns[parts[3]]
                if parts[-1] == "report.md":
                    file = (
                        self.server.store.root
                        / "campaigns"
                        / campaign["id"]
                        / "report.md"
                    )
                    self.reply(
                        file.read_bytes(), content_type="text/markdown; charset=utf-8"
                    )
                else:
                    self.reply(campaign)
                return
            if path.startswith("/api/runs/"):
                self.browser()
                parts = path.split("/")
                job = self.server.store.jobs[parts[3]]
                if len(parts) == 5 and parts[4] == "report.json":
                    self.reply(job)
                elif len(parts) == 6 and parts[4] == "files":
                    content = job["files"][parts[5]]
                    self.reply(
                        content.encode(), content_type="text/plain; charset=utf-8"
                    )
                else:
                    self.reply({"error": "File not found"}, 404)
                return
            file = (
                STATIC / ("index.html" if path == "/" else path.lstrip("/"))
            ).resolve()
            if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
                self.reply({"error": "Not found"}, 404)
                return
            self.reply(
                file.read_bytes(),
                content_type=mimetypes.guess_type(file.name)[0]
                or "application/octet-stream",
                cookie=path == "/",
            )
        except PermissionError as error:
            self.reply({"error": str(error)}, 403)
        except (KeyError, ValueError, IndexError):
            self.reply({"error": "Not found"}, 404)

    def do_POST(self):
        try:
            if not self.valid_host():
                raise PermissionError("Loopback Host required")
            origin = self.headers.get("Origin")
            if origin and origin not in {
                f"http://localhost:{self.server.server_port}",
                f"http://127.0.0.1:{self.server.server_port}",
            }:
                raise PermissionError("Cross-origin requests refused")
            if self.headers.get("Content-Type") != "application/json":
                raise ValueError("JSON request required")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8_000_000:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(length))
            path, store = self.path, self.server.store
            if path == "/worker/register":
                self.reply(
                    store.register(
                        data["pairing"],
                        data["label"],
                        data["identity"],
                        data.get("launchers"),
                        data.get("build"),
                    )
                )
                return
            if path.startswith("/worker/"):
                worker = self.worker()
                if path == "/worker/poll":
                    with store.lock:
                        worker["identity"] = data["identity"]
                        if "launchers" in data:
                            worker["launchers"] = data["launchers"]
                        if "build" in data:
                            worker["build"] = data["build"]
                    store.rebind_targets(worker)
                    store.sync_stacks(worker)
                    self.reply({"job": store.claim(worker)})
                elif path == "/worker/update":
                    self.reply(store.update(worker, data["job_id"], data))
                else:
                    self.reply({"error": "Not found"}, 404)
                return
            self.browser()
            if path == "/api/pair":
                code = store.pairing()
                command = (
                    "cd " + shlex.quote(str(Path(__file__).resolve().parents[1])) + "\n"
                )
                command += (
                    "python3 -m ringfour_acceptance worker \\\n  --server http://127.0.0.1:"
                    + str(self.server.server_port)
                )
                command += " \\\n  --platform " + shlex.quote(str(self.server.platform))
                command += " \\\n  --virtual " + shlex.quote(
                    str(self.server.virtual_root)
                )
                command += " \\\n  --pair " + code
                self.reply({"code": code, "expires_in": 300, "command": command})
            elif path == "/api/stacks":
                self.reply({"stack": store.add_stack(data["worker_id"])}, 201)
            elif path == "/api/stacks/configure":
                self.reply(
                    store.configure_stack(
                        data["stack_id"], data["worker_id"], data["allow_dirty"]
                    )
                )
            elif path == "/api/stacks/action":
                self.reply(
                    {"job": store.stack_action(data["stack_id"], data["action"])}, 201
                )
            elif path == "/api/virtual/plan":
                self.reply(store.virtual_plan(data["worker_id"], data["spec"]))
            elif path == "/api/virtual/run":
                self.reply(
                    {
                        "job": store.enqueue_virtual(
                            data["worker_id"], data["spec"], data["fingerprint"]
                        )
                    },
                    201,
                )
            elif path == "/api/environments":
                self.reply(store.save_environment(data))
            elif path == "/api/campaigns":
                self.reply(
                    store.create_campaign(data["environment_id"], data["scope"]), 201
                )
            elif path == "/api/campaigns/cancel":
                self.reply(store.cancel_campaign(data["campaign_id"]))
            elif path == "/api/campaigns/cleanup":
                self.reply(store.recover_campaign_cleanup(data["campaign_id"]))
            elif path == "/api/campaigns/evidence":
                self.reply(
                    store.record_campaign_evidence(
                        data["campaign_id"],
                        data["case_id"],
                        data["outcome"],
                        data["reference"],
                        data.get("note", ""),
                        data["operator"],
                    )
                )
            elif path == "/api/setup":
                self.reply(store.configure(data))
            elif path == "/api/runs":
                self.reply(
                    {"jobs": store.enqueue(data["profile"], data.get("worker_id"))}, 201
                )
            elif path == "/api/cancel":
                self.reply(store.cancel(data["job_id"]))
            else:
                self.reply({"error": "Not found"}, 404)
        except PermissionError as error:
            self.reply({"error": str(error)}, 403)
        except (ValueError, KeyError, TypeError) as error:
            self.reply({"error": str(error)}, 400)
