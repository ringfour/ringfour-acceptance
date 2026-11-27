"""Stationary AP probe: heartbeat/settings/evidence/decisions or two camera frames."""

import asyncio
import binascii
import json
import math
import os
import signal
import struct
import threading
import time
import zlib
from pathlib import Path

from ringfour_runtime import artifacts, interactions, logbook
from ringfour_runtime.settings import SettingsClient, SettingsRejected

stop = threading.Event()
initialized = threading.Event()
values = {"sample_interval_ms": 250, "gain": 0.25}
root = Path(os.environ["RINGFOUR_ARTIFACT_DIR"])
config = json.loads(Path(os.environ["RINGFOUR_APP_CONFIG"]).read_text())


def emit(event, **data):
    print(json.dumps({"event": event, **data}, allow_nan=False), flush=True)


def apply(candidate):
    if candidate["sample_interval_ms"] == 777:
        raise SettingsRejected("The AP fixture deliberately rejects 777 ms.")
    gain = candidate["gain"]
    if type(gain) not in (int, float) or not math.isfinite(gain) or not -1 <= gain <= 1:
        raise SettingsRejected("Gain must be finite and within -1..1")
    values.update(candidate)
    emit("ap.settings", **candidate)
    initialized.set()


def png(path, image):
    stride = max(1, math.ceil(image.shape[1] / 640))
    rgb = image[::stride, ::stride, :3]
    height, width = rgb.shape[:2]
    pixels = rgb.tobytes(order="C")
    scan = b"".join(
        b"\0" + pixels[n : n + width * 3] for n in range(0, len(pixels), width * 3)
    )

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scan))
        + chunk(b"IEND", b"")
    )


async def camera():
    from ringfour.aio import Robot

    async with await Robot.connect(
        os.environ["RINGFOUR_AGENT_URL"], timeout=5.0, transport="net"
    ) as robot:
        if not robot.has("cameras") or not robot.cameras:
            raise RuntimeError("No camera is advertised")
        source = (
            robot.cameras["base"]
            if "base" in robot.cameras
            else robot.cameras[min(robot.cameras)]
        )
        for n in range(2):
            frame = await source.get_frame()
            path = root / f"photo-{n + 1:02d}.png"
            png(path, frame.image)
            identifier = artifacts.publish_file(path, content_type="image/png")
            emit("ap.photo", artifact_id=identifier, stamp=frame.stamp)
            await asyncio.sleep(1)


for signum in (signal.SIGTERM, signal.SIGINT):
    signal.signal(signum, lambda *_: stop.set())
client = SettingsClient()
thread = threading.Thread(
    target=client.serve,
    args=(apply,),
    kwargs={"poll_seconds": 1, "stop_event": stop},
    daemon=True,
)
thread.start()
try:
    if not initialized.wait(60):
        raise RuntimeError("Initial settings were not confirmed")
    proof = root / "proof.bin"
    proof.write_bytes(b"ringfour-ap-immutable-evidence-v1")
    artifact = artifacts.publish_file(proof, content_type="application/octet-stream")
    proof.write_bytes(b"changed after immutable publication")
    proof.unlink()
    logbook.info("AP probe ready", evidence=[artifact], label=config["label"])
    request = interactions.create(
        "AP verification: continue?", {"yes": "Continue", "no": "Abort safely"}
    )
    emit(
        "ap.ready",
        artifact_id=artifact,
        request_id=request.request_id,
        label=config["label"],
    )
    if config["mode"] == "camera":
        asyncio.run(camera())
    else:
        deadline = time.monotonic() + 1800
        while not stop.wait(0.5) and time.monotonic() < deadline:
            emit("ap.tick", **values)
finally:
    stop.set()
    thread.join(6)
    emit("ap.finished")
