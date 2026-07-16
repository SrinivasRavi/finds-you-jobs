"""Covers: skeleton — the real sidecar process, end to end (architecture §4.4).

Boots `python -m sidecar.app` as a subprocess (exactly as the shell does),
parses the PORT/TOKEN handshake off stdout, then hits the live loopback server:
healthz (open), 401 without token, and a clean /shutdown that exits the
process. No mocks — this is the "boot the real thing" gate as a test.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

HANDSHAKE_TIMEOUT_S = 20.0
_PORT_RE = re.compile(r"^PORT=(\d+)$")
_TOKEN_RE = re.compile(r"^TOKEN=(.+)$")


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    """Point the child sidecar at throwaway data/log dirs so tests never touch
    the developer's real app-data location."""
    return {
        **os.environ,
        "FYJ_DATA_DIR": str(tmp_path / "data"),
        "FYJ_LOG_DIR": str(tmp_path / "logs"),
    }


def _read_handshake(proc: subprocess.Popen[str]) -> tuple[int, str]:
    port: int | None = None
    token: str | None = None
    deadline = time.monotonic() + HANDSHAKE_TIMEOUT_S
    assert proc.stdout is not None
    while time.monotonic() < deadline and (port is None or token is None):
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError("sidecar exited before completing handshake")
            continue
        line = line.strip()
        if m := _PORT_RE.match(line):
            port = int(m.group(1))
        elif m := _TOKEN_RE.match(line):
            token = m.group(1)
    if port is None or token is None:
        raise RuntimeError("handshake not received within timeout")
    return port, token


@pytest.fixture
def sidecar(tmp_path: Path) -> Iterator[tuple[str, str]]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "sidecar.app"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=_isolated_env(tmp_path),
    )
    try:
        port, token = _read_handshake(proc)
        base = f"http://127.0.0.1:{port}"
        # Wait for the port to actually accept connections.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        yield base, token
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_healthz_live(sidecar: tuple[str, str]) -> None:
    base, _ = sidecar
    resp = httpx.get(f"{base}/healthz", timeout=5)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_shutdown_401_without_token_live(sidecar: tuple[str, str]) -> None:
    base, _ = sidecar
    resp = httpx.post(f"{base}/shutdown", timeout=5)
    assert resp.status_code == 401


def test_events_401_without_token_live(sidecar: tuple[str, str]) -> None:
    base, _ = sidecar
    resp = httpx.get(f"{base}/api/events", timeout=5)
    assert resp.status_code == 401


def test_events_streams_heartbeat_live(sidecar: tuple[str, str]) -> None:
    base, token = sidecar
    with httpx.stream("GET", f"{base}/api/events?token={token}", timeout=5) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                assert '"type":"heartbeat"' in line
                break


def test_shutdown_exits_process_live(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "sidecar.app"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=_isolated_env(tmp_path),
    )
    try:
        port, token = _read_handshake(proc)
        base = f"http://127.0.0.1:{port}"
        time.sleep(0.5)
        resp = httpx.post(
            f"{base}/shutdown",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        assert resp.status_code == 200
        # The process must exit on its own after /shutdown.
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
