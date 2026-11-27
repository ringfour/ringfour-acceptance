"""Provision a public pinned template using the current worker's own permissions."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

SHARED_TEMPLATES = Path("/tmp/ringfour-acceptance-templates")
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024


def cached_template(pointer, data_root, shared_root=SHARED_TEMPLATES):
    name, digest = pointer["archive_name"], pointer["sha256"]
    if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid pinned template identity")
    destination = Path(data_root) / "app-templates/robot-check" / name

    def verified(path):
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("Pinned template exceeds the archive size limit")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Pinned Robot Check template checksum mismatch")
        return content

    if destination.is_symlink():
        raise ValueError("Pinned template cache cannot use a symlink")
    if destination.is_file():
        verified(destination)
        return destination
    source = Path(shared_root) / digest / name
    if not source.is_file():
        raise ValueError(
            f"Missing pinned Robot Check template. Seed {source} or provide {destination} for this worker account"
        )
    content = verified(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".template-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination
