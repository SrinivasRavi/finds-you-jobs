"""Covers: A1 scaffold — the PORT/TOKEN handshake contract (architecture §4.4).

The shell parses two flushed stdout lines; these tests pin that exact format and
the free-port helper.
"""

from __future__ import annotations

import re
import socket

from sidecar.app.__main__ import emit_handshake, find_free_port


def test_find_free_port_is_bindable() -> None:
    port = find_free_port()
    assert 1024 < port < 65536
    # The port the OS handed back must actually be bindable on loopback.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))


def test_emit_handshake_exact_two_line_format(capsys) -> None:
    emit_handshake(54321, "11111111-2222-3333-4444-555555555555")
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines == [
        "PORT=54321",
        "TOKEN=11111111-2222-3333-4444-555555555555",
    ]


def test_handshake_lines_are_machine_parseable() -> None:
    # The regex the Rust shell uses (PORT=<digits>, TOKEN=<uuid-ish>).
    port_re = re.compile(r"^PORT=(\d+)$")
    token_re = re.compile(r"^TOKEN=([0-9a-fA-F-]{36})$")
    assert port_re.match("PORT=8000")
    assert token_re.match("TOKEN=11111111-2222-3333-4444-555555555555")
    assert not port_re.match("PORT=")


def test_dev_handshake_file_is_owner_only(tmp_path, monkeypatch) -> None:
    """F-L4: the opt-in handshake file carries the bearer token — 0600, always,
    including when a stale world-readable file is being overwritten."""
    import json
    import os
    import stat

    from sidecar.app.__main__ import _maybe_write_dev_handshake

    target = tmp_path / "handshake.json"
    monkeypatch.setenv("FYJ_WRITE_HANDSHAKE", str(target))
    _maybe_write_dev_handshake(4321, "tok-abc")
    assert json.loads(target.read_text()) == {"port": 4321, "token": "tok-abc"}
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        # A pre-existing loose file gets clamped on rewrite.
        os.chmod(target, 0o644)
        _maybe_write_dev_handshake(4322, "tok-def")
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert json.loads(target.read_text())["port"] == 4322


def test_dev_handshake_noop_without_env(tmp_path, monkeypatch) -> None:
    from sidecar.app.__main__ import _maybe_write_dev_handshake

    monkeypatch.delenv("FYJ_WRITE_HANDSHAKE", raising=False)
    _maybe_write_dev_handshake(1, "t")
    assert list(tmp_path.iterdir()) == []
