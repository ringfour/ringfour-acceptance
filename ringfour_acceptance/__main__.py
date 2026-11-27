import argparse
import signal
import threading
from pathlib import Path

from .server import Server
from .worker import run


def main():
    parser = argparse.ArgumentParser(description="Ringfour acceptance control room")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="Open the UI and connect this account's worker"
    )
    serve.add_argument("--port", type=int, default=8095)
    serve.add_argument("--state", type=Path, default=Path(".state"))
    serve.add_argument("--platform", type=Path, default=Path("../ringfour-platform"))
    serve.add_argument(
        "--virtual",
        type=Path,
        help="Virtual launcher checkout (default: Platform sibling)",
    )
    serve.add_argument("--no-worker", action="store_true")
    worker = commands.add_parser(
        "worker", help="Connect a worker under another account"
    )
    worker.add_argument("--server", default="http://127.0.0.1:8095")
    worker.add_argument("--platform", type=Path, default=Path("../ringfour-platform"))
    worker.add_argument("--pair", required=True)
    worker.add_argument("--label")
    worker.add_argument(
        "--virtual",
        type=Path,
        help="Virtual launcher checkout (default: Platform sibling)",
    )
    args = parser.parse_args()
    if args.command == "worker":
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            run(args.server, args.platform, args.pair, args.label, stop, args.virtual)
        except (OSError, ValueError) as error:
            parser.exit(2, f"Worker connection failed: {error}\n")
        return
    server = Server(("127.0.0.1", args.port), args.state, args.platform, args.virtual)
    stop = threading.Event()
    thread = None
    if not args.no_worker:
        code = server.store.pairing()
        thread = threading.Thread(
            target=run,
            args=(f"http://127.0.0.1:{server.server_port}", args.platform, code),
            kwargs={"stop": stop, "virtual_root": args.virtual},
            daemon=True,
        )
        thread.start()
    print(f"Ringfour Acceptance: http://127.0.0.1:{server.server_port}", flush=True)
    print("No tests start until you select Run in the UI.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("Stopping; waiting for worker cleanup…", flush=True)
    finally:
        # Keep HTTP available while an active worker submits its cleanup result.
        stop.set()
        cleanup_server = threading.Thread(target=server.serve_forever, daemon=True)
        cleanup_server.start()
        if thread:
            thread.join()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
