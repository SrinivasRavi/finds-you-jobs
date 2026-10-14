"""Shared Claude-subscription engine.

Every module calls Claude through the logged-in `claude` CLI in print mode, as a
subprocess, with no API key. The model is pinned per run (`DEFAULT_MODEL`).
Module engines wrap this and convert `EngineError`/`EngineUsage` into their own
typed contract."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"

# Env vars that flip the `claude` CLI from subscription to pay-per-token
# billing. The provider is "Claude subscription", so every subprocess scrubs them.
# (Idea credited to JustHireMe, reimplemented; see THIRD_PARTY_NOTICES.md.)
_SCRUB_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

_CLAUDE_PATH: str | None = None


def _subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _SCRUB_ENV:
        env.pop(key, None)
    return env


def resolve_claude(refresh: bool = False) -> str | None:
    """Locate `claude` the way the user's terminal would. A GUI-launched app gets a
    minimal PATH, so when `shutil.which` misses, fall back to the login shell's PATH.
    Cached once found; `refresh` re-probes (after an install)."""
    global _CLAUDE_PATH
    if _CLAUDE_PATH and not refresh:
        return _CLAUDE_PATH
    direct = shutil.which("claude")
    if direct:
        _CLAUDE_PATH = direct
        return direct
    if os.name == "nt":  # no POSIX login-shell trick on Windows
        return None
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        proc = subprocess.run(  # noqa: S603
            [shell, "-l", "-i", "-c", "command -v claude"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = proc.stdout.strip()
    found = out.splitlines()[-1].strip() if out else ""
    if found:
        _CLAUDE_PATH = found
    return found or None


def claude_auth_status(exe: str | None = None) -> dict[str, object] | None:
    """Login state via `claude auth status`, instant and free: `{logged_in, email,
    plan}`, or None when it can't answer (callers then try a minimal completion).
    Scrubbed env, for the subscription login."""
    exe = exe or resolve_claude()
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [exe, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
            env=_subscription_env(),
        )
        payload = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "loggedIn" not in payload:
        return None
    return {
        "logged_in": bool(payload.get("loggedIn")),
        "email": payload.get("email"),
        "plan": payload.get("subscriptionType"),
    }


@dataclass
class EngineUsage:
    internal_calls: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    # Prompt-cache token counts (the CLI harness caches its system prompt; these
    # dominate cost on small prompts). Needed for exact cross-model repricing.
    tokens_cache_write: int | None = None
    tokens_cache_read: int | None = None
    usd: float | None = None
    latency_ms: int | None = None
    model: str | None = None


class EngineError(RuntimeError):
    """An engine call failed; the message is the verbatim cause. Structured fields
    for the retry policy (F-H5): `status` (HTTP status), `retryable` (an explicit
    override), `retry_after_s` (the provider's hint), and `provider` (the service
    that failed, when not the routed engine), which the breaker charges."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool | None = None,
        retry_after_s: float | None = None,
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.provider = provider


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """Best-effort kill of the child's whole process group / tree (F-L9)."""
    try:
        if os.name == "nt":
            # No POSIX process groups; `taskkill /T` walks and kills the tree
            # (grandchildren holding inherited pipe handles included) — the
            # stdlib-driveable equivalent of killpg. Absolute path (S607).
            taskkill = os.path.join(
                os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"
            )
            subprocess.run(  # noqa: S603
                [taskkill, "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=15,
            )
        else:
            # start_new_session=True made the child its own group leader, so
            # its pid IS the pgid — one killpg takes the CLI and every tool
            # subprocess it spawned.
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            proc.kill()  # fall back to the direct child alone
        except OSError:
            pass


def run_cli(
    cmd: list[str],
    *,
    input: str | None = None,  # noqa: A002 — mirrors subprocess.run's keyword
    timeout: float | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run` that starts the CLI in its own process group and kills the
    whole group on timeout (F-L9). The CLIs spawn tool subprocesses, and plain `run`
    left them alive on POSIX and blocked forever on Windows. Still raises
    `TimeoutExpired`."""
    # Windows: CREATE_NEW_PROCESS_GROUP (the attribute only exists there).
    # POSIX: start_new_session → setsid, so the child leads its own group.
    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            # The group is dead, so the pipes close and this reaps promptly. A
            # pathological survivor (double-forked daemon holding the pipe)
            # hits the short cap and is abandoned rather than wedging the
            # worker thread.
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


class ClaudeCliEngine:
    """`claude -p` on the maintainer's subscription. `cwd` and `extra_args` serve
    repo-rooted agent callers (the career-ops parity runner)."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout_s: int = 600,
        cwd: Path | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.cwd = cwd
        self.extra_args = extra_args

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        exe = resolve_claude()
        if exe is None:
            # Deterministic: retrying can't materialize a missing binary.
            raise EngineError("`claude` CLI not found on PATH", retryable=False)
        cmd = [exe, "-p", "--model", self.model, "--output-format", "json"]
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]
        cmd += list(self.extra_args)
        started = time.monotonic()
        try:
            proc = run_cli(
                cmd,
                input=user_prompt,
                timeout=self.timeout_s,
                cwd=self.cwd,
                env=_subscription_env(),
            )
        except subprocess.TimeoutExpired as e:
            # The full per-attempt budget is already spent — an immediate
            # same-prompt retry would hold the llm slot for another full
            # timeout with poor odds. Fail fast; the user's Retry button (or
            # the runner's queue) is the honest path (F-H5).
            raise EngineError(
                f"claude CLI timed out after {self.timeout_s}s", retryable=False
            ) from e
        latency_ms = int((time.monotonic() - started) * 1000)
        if proc.returncode != 0:
            raise EngineError(
                f"claude CLI exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:2000]}"
            )
        try:
            payload = json.loads(proc.stdout)
            text = payload.get("result", "")
            usage_raw = payload.get("usage") or {}
            usage = EngineUsage(
                internal_calls=1,
                tokens_in=usage_raw.get("input_tokens"),
                tokens_out=usage_raw.get("output_tokens"),
                tokens_cache_write=usage_raw.get("cache_creation_input_tokens"),
                tokens_cache_read=usage_raw.get("cache_read_input_tokens"),
                usd=payload.get("total_cost_usd"),
                latency_ms=latency_ms,
                model=payload.get("model", self.model),
            )
        except json.JSONDecodeError:
            # Older CLI or plain-text mode: keep the text, record what we know.
            text = proc.stdout
            usage = EngineUsage(internal_calls=1, latency_ms=latency_ms, model=self.model)
        if not text.strip():
            raise EngineError(f"claude CLI returned empty result; stderr: {proc.stderr[:500]}")
        return text, usage
