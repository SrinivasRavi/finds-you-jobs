"""Covers: the subscription-CLI engine family (`_shared/cli_engines.py`).

Fake `subprocess.run` throughout — no real CLI, no network. The contracts under
test are the ones the engines promise the app:

- Codex: containment flags (`--sandbox read-only`, scratch cwd, prompt via
  stdin), billing scrub (OPENAI_API_KEY never reaches the child), JSONL
  parsing (agent message + usage, tolerant of schema variants), honest errors
  (missing binary, nonzero exit, empty result).
- Antigravity: scratch cwd + GEMINI/GOOGLE key scrub, and the empty-stdout
  case surfacing as a clear EngineError naming the upstream non-TTY bug —
  never a silent empty completion.
- `codex login status` probe mapping (ok / not_logged_in / unknown→None).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import pytest

from sidecar.modules._shared import cli_engines as ce
from sidecar.modules._shared.claude_engine import EngineError

# Captured at import time — the autouse fixture below replaces the module
# attribute, so the cache test needs the genuine function.
_REAL_RESOLVE_CLI = ce.resolve_cli


class FakeProc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class RunRecorder:
    """Captures the subprocess.run call the engine makes."""

    def __init__(self, proc: FakeProc) -> None:
        self.proc = proc
        self.cmd: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.cwd_existed = False

    def __call__(self, cmd: list[str], **kwargs: Any) -> FakeProc:
        self.cmd = list(cmd)
        self.kwargs = kwargs
        cwd = kwargs.get("cwd")
        self.cwd_existed = bool(cwd) and os.path.isdir(str(cwd))
        return self.proc


def _codex_jsonl() -> str:
    lines = [
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"item_type": "agent_message", "text": "hello from codex"},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 8},
        },
    ]
    return "\n".join(json.dumps(x) for x in lines)


@pytest.fixture(autouse=True)
def _pinned_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every CLI instantly (no login-shell probe) unless a test says
    otherwise; clear the path cache so tests never leak into each other."""
    monkeypatch.setattr(ce, "_PATH_CACHE", {})
    monkeypatch.setattr(ce, "resolve_cli", lambda binary, refresh=False: f"/fake/bin/{binary}")


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def test_codex_complete_containment_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stdout=_codex_jsonl()))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-reach-child")

    text, usage = ce.CodexCliEngine().complete("SYSTEM RULES", "USER TASK")

    assert text == "hello from codex"
    assert usage.tokens_in == 120
    assert usage.tokens_out == 8
    assert usage.tokens_cache_read == 40
    assert usage.internal_calls == 1
    # Containment: read-only sandbox, prompt on stdin, fresh scratch cwd.
    assert "--sandbox" in rec.cmd and rec.cmd[rec.cmd.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in rec.cmd
    assert rec.cmd[-1] == "-"
    assert rec.kwargs["input"] == "SYSTEM RULES\n\nUSER TASK"
    assert rec.cwd_existed  # the scratch dir existed at call time
    # Billing scrub: the subscription pays, never a stray API key.
    assert "OPENAI_API_KEY" not in rec.kwargs["env"]
    # No model routed → no -m flag; the CLI's own default runs.
    assert "--model" not in rec.cmd


def test_codex_complete_model_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stdout=_codex_jsonl()))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    ce.CodexCliEngine(model="gpt-5-codex").complete("", "task")
    assert rec.cmd[rec.cmd.index("--model") + 1] == "gpt-5-codex"
    assert rec.kwargs["input"] == "task"  # empty system prompt is not prepended


def test_codex_top_level_last_agent_message_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = json.dumps({"type": "turn.completed", "last_agent_message": "top-level text"})
    rec = RunRecorder(FakeProc(stdout=stdout))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    text, _ = ce.CodexCliEngine().complete("", "task")
    assert text == "top-level text"


def test_codex_unparseable_stdout_degrades_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stdout="plain text answer, older CLI"))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    text, usage = ce.CodexCliEngine().complete("", "task")
    assert text == "plain text answer, older CLI"
    assert usage.tokens_in is None


def test_codex_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ce, "resolve_cli", lambda binary, refresh=False: None)
    with pytest.raises(EngineError, match="not found"):
        ce.CodexCliEngine().complete("", "task")


def test_codex_nonzero_exit_carries_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stderr="stream error: login required", returncode=1))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    with pytest.raises(EngineError, match="login required"):
        ce.CodexCliEngine().complete("", "task")


def test_codex_empty_result_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}})
    rec = RunRecorder(FakeProc(stdout=empty))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    with pytest.raises(EngineError, match="empty result"):
        ce.CodexCliEngine().complete("", "task")


def test_codex_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: list[str], **kwargs: Any) -> FakeProc:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    monkeypatch.setattr(ce.subprocess, "run", boom)
    with pytest.raises(EngineError, match="timed out"):
        ce.CodexCliEngine(timeout_s=5).complete("", "task")


# ---------------------------------------------------------------------------
# codex login status probe
# ---------------------------------------------------------------------------


def test_codex_login_status_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stdout="Logged in using ChatGPT (user@example.com)\n"))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    probe = ce.codex_login_status("/fake/bin/codex")
    assert probe is not None
    assert probe.status == "ok"
    assert "user@example.com" in probe.detail
    assert rec.cmd[-2:] == ["login", "status"]
    assert "OPENAI_API_KEY" not in rec.kwargs["env"]


def test_codex_login_status_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stderr="Not logged in. Run codex login.", returncode=1))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    probe = ce.codex_login_status("/fake/bin/codex")
    assert probe is not None
    assert probe.status == "not_logged_in"


def test_codex_login_status_unknown_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stderr="unknown subcommand: status", returncode=2))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    assert ce.codex_login_status("/fake/bin/codex") is None


# ---------------------------------------------------------------------------
# Antigravity
# ---------------------------------------------------------------------------


def test_agy_complete_scrub_and_scratch_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stdout="the answer\n"))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    monkeypatch.setenv("GEMINI_API_KEY", "leak")
    monkeypatch.setenv("GOOGLE_API_KEY", "leak")

    text, usage = ce.AntigravityCliEngine().complete("SYS", "TASK")

    assert text == "the answer"
    assert usage.internal_calls == 1
    assert usage.tokens_in is None  # agy exposes no usage — recorded honestly
    assert rec.cmd[1] == "-p"
    assert rec.cmd[2] == "SYS\n\nTASK"
    assert rec.cwd_existed
    assert "GEMINI_API_KEY" not in rec.kwargs["env"]
    assert "GOOGLE_API_KEY" not in rec.kwargs["env"]


def test_agy_empty_stdout_names_upstream_bug(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = RunRecorder(FakeProc(stdout=""))
    monkeypatch.setattr(ce.subprocess, "run", rec)
    with pytest.raises(EngineError, match="non-interactive"):
        ce.AntigravityCliEngine().complete("", "task")


def test_agy_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ce, "resolve_cli", lambda binary, refresh=False: None)
    with pytest.raises(EngineError, match="Antigravity"):
        ce.AntigravityCliEngine().complete("", "task")


# ---------------------------------------------------------------------------
# resolve_cli cache
# ---------------------------------------------------------------------------


def test_resolve_cli_caches_per_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drives the genuine resolver (the autouse fixture pins the module attr).
    calls: list[str] = []

    def fake_which(binary: str) -> str | None:
        calls.append(binary)
        return f"/fake/bin/{binary}"

    monkeypatch.setattr(ce.shutil, "which", fake_which)
    monkeypatch.setattr(ce, "_PATH_CACHE", {})
    assert _REAL_RESOLVE_CLI("codex") == "/fake/bin/codex"
    assert _REAL_RESOLVE_CLI("codex") == "/fake/bin/codex"
    assert calls == ["codex"]  # second hit served from cache
    assert _REAL_RESOLVE_CLI("agy") == "/fake/bin/agy"
    assert calls == ["codex", "agy"]  # cache is per-binary
