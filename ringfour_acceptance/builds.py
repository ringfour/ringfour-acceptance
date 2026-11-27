"""Non-secret source/build identity. Deployment identity is recorded separately."""

import hashlib
import json
import subprocess
from pathlib import Path


def source(root):
    root = Path(root).resolve()
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        tracked = subprocess.run(
            ["git", "-C", str(root), "diff", "--binary", "HEAD"],
            capture_output=True,
            timeout=15,
            check=True,
        ).stdout
        untracked = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.split(b"\0")
        digest = hashlib.sha256(tracked)
        count = 0
        for raw in sorted(filter(None, untracked)):
            relative = raw.decode(errors="surrogateescape")
            if relative.startswith(("docs/evidence/", "test-results/")):
                continue
            file = root / relative
            if file.is_file() and not file.is_symlink():
                digest.update(raw)
                digest.update(hashlib.sha256(file.read_bytes()).digest())
                count += 1
        return {
            "root": str(root),
            "commit": commit,
            "dirty": bool(tracked or count),
            "working_tree_sha256": digest.hexdigest(),
        }
    except (OSError, subprocess.SubprocessError):
        return {"root": str(root), "error": "Git source identity unavailable"}


def snapshot(platform):
    platform = Path(platform).resolve()
    sources = {
        name: source(path)
        for name, path in {
            "platform": platform,
            "agent": platform.parent / "ringfour-agent",
            "sdk": platform.parent / "ringfour-sdk",
            "robot_check": platform.parent / "robot-check",
        }.items()
    }
    runner = hashlib.sha256()
    package = Path(__file__).parent
    for file in sorted([*package.glob("*.py"), *package.glob("fixtures/*.py")]):
        runner.update(str(file.relative_to(package)).encode())
        runner.update(file.read_bytes())
    runner_sha256 = runner.hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps(
            {"sources": sources, "runner_sha256": runner_sha256}, sort_keys=True
        ).encode()
    ).hexdigest()
    return {
        "compliance": True,
        "catalog_version": 1,
        "platform_root": str(platform),
        "sources": sources,
        "runner_sha256": runner_sha256,
        "fingerprint": fingerprint,
        "deployment_identity_note": "Git identifies the test source checkout. Stack image IDs and API URL identify the observed deployment; a label alone does not prove deployed source equality.",
    }
