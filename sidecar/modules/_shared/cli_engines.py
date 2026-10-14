"""Subscription-CLI engines: Codex (ChatGPT plan) and Antigravity (Google AI).

The user's logged-in CLI runs as a print-mode subprocess, so no key enters the
app; each engine matches `ClaudeCliEngine`'s contract and types. These CLIs are
agents, so every call runs in an empty scratch dir, and Codex with
`--sandbox read-only`: an injected JD must not touch the user's files.
Antigravity auto-approves tools, hence experimental. Env vars that would switch
to API billing are scrubbed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

from .claude_engine import EngineError, EngineUsage, run_cli

# Binary path cache, per CLI name — same rationale as claude_engine._CLAUDE_PATH
# (a GUI-launched app inherits a minimal PATH; the login-shell probe is slow).
_PATH_CACHE: dict[str, str] = {}

_CODEX_SCRUB = ("OPENAI_API_KEY",)
_ANTIGRAVITY_SCRUB = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def _scrubbed_env(scrub: tuple[str, ...]) -> dict[str, str]:
    env = dict(os.environ)
    for key in scrub:
        env.pop(key, None)
    return env


def resolve_cli(binary: str, refresh: bool = False) -> str | None:
    """Locate `binary` the way the user's terminal would (direct PATH, then the
    login+interactive shell's PATH — see `claude_engine.resolve_claude` for the
    full GUI-launch rationale). Cached per binary; `refresh` re-probes."""
    cached = _PATH_CACHE.get(binary)
    if cached and not refresh:
        return cached
    direct = shutil.which(binary)
    if direct:
        _PATH_CACHE[binary] = direct
        return direct
    if os.name == "nt":  # no POSIX login-shell trick on Windows
        return None
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        proc = subprocess.run(  # noqa: S603
            [shell, "-l", "-i", "-c", f"command -v {binary}"],
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
        _PATH_CACHE[binary] = found
    return found or None


@dataclass
class CliProbe:
    """A cheap auth/health probe result the verify endpoint maps onto its DTO."""

    status: str  # "ok" | "not_found" | "not_logged_in" | "error"
    detail: str


def codex_login_status(exe: str | None = None) -> CliProbe | None:
    """Login state via `codex login status` — instant, no LLM call. Returns
    None when the probe can't answer (binary missing, unknown output) so the
    caller falls back to a minimal real completion."""
    exe = exe or resolve_cli("codex")
    if exe is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [exe, "login", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
            env=_scrubbed_env(_CODEX_SCRUB),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    first = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")
    if proc.returncode == 0:
        return CliProbe(status="ok", detail=first or "Logged in to Codex")
    low = out.lower()
    if "not logged in" in low or "login" in low:
        return CliProbe(status="not_logged_in", detail=first or "Codex CLI is not logged in.")
    return None


class CodexCliEngine:
    """`codex exec` on the ChatGPT subscription: `--json` events (plain stdout as
    fallback), `--sandbox read-only` in an empty cwd, no `-m` when no model is set
    (the user's own default), and the system prompt prepended (no flag for it)."""

    def __init__(self, model: str | None = None, timeout_s: int = 600) -> None:
        self.model = model or None
        self.timeout_s = timeout_s

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        exe = resolve_cli("codex")
        if exe is None:
            raise EngineError("`codex` CLI not found on PATH", retryable=False)
        cmd = [exe, "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check"]
        if self.model:
            cmd += ["--model", self.model]
        cmd += ["-"]  # prompt on stdin — argv has size limits, resumes+JDs don't
        prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="fyj-codex-") as scratch:
            try:
                # run_cli (not subprocess.run): group launch + group kill on
                # timeout, so agent-spawned grandchildren die too (F-L9).
                proc = run_cli(
                    cmd,
                    input=prompt,
                    timeout=self.timeout_s,
                    cwd=scratch,
                    env=_scrubbed_env(_CODEX_SCRUB),
                )
            except subprocess.TimeoutExpired as e:
                # Full per-attempt budget spent — fail fast, don't hold the
                # llm slot for another full timeout (F-H5).
                raise EngineError(
                    f"codex CLI timed out after {self.timeout_s}s", retryable=False
                ) from e
        latency_ms = int((time.monotonic() - started) * 1000)
        if proc.returncode != 0:
            raise EngineError(
                f"codex CLI exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:2000]}"
            )
        text, usage = _parse_codex_jsonl(proc.stdout)
        usage.latency_ms = latency_ms
        usage.model = usage.model or self.model
        if not text.strip():
            raise EngineError(f"codex CLI returned empty result; stderr: {proc.stderr[:500]}")
        return text, usage


def _parse_codex_jsonl(stdout: str) -> tuple[str, EngineUsage]:
    """Pull the last agent message + usage out of `codex exec --json` output.

    Tolerant across CLI versions: any JSON line whose item/payload looks like an
    agent message contributes text (last one wins); any line carrying a usage
    object contributes token counts. Zero parseable lines → stdout as-is."""
    text = ""
    usage = EngineUsage(internal_calls=1)
    parsed_any = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue
        parsed_any = True
        item = evt.get("item")
        if isinstance(item, dict):
            item_type = item.get("item_type") or item.get("type")
            if item_type == "agent_message" and isinstance(item.get("text"), str):
                text = item["text"]
        # Some versions put the final message at the top level.
        if isinstance(evt.get("last_agent_message"), str):
            text = evt["last_agent_message"]
        raw_usage = evt.get("usage")
        if isinstance(raw_usage, dict):
            usage.tokens_in = raw_usage.get("input_tokens", usage.tokens_in)
            usage.tokens_out = raw_usage.get("output_tokens", usage.tokens_out)
            usage.tokens_cache_read = raw_usage.get(
                "cached_input_tokens", usage.tokens_cache_read
            )
        if isinstance(evt.get("model"), str):
            usage.model = evt["model"]
    if not parsed_any:
        text = stdout
    return text, usage


class AntigravityCliEngine:
    """`agy -p` on the Google AI subscription. Experimental: plain-text output (no
    token usage), `-p` without a TTY can drop the response (a clear EngineError,
    which Verify catches at setup), and tools auto-approve (hence the empty cwd).
    No model flag."""

    def __init__(self, model: str | None = None, timeout_s: int = 600) -> None:
        self.model = model or None  # accepted for the factory contract; unused
        self.timeout_s = timeout_s

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        exe = resolve_cli("agy")
        if exe is None:
            raise EngineError("`agy` (Antigravity CLI) not found on PATH", retryable=False)
        prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="fyj-agy-") as scratch:
            try:
                # run_cli (not subprocess.run): group launch + group kill on
                # timeout, so agent-spawned grandchildren die too (F-L9).
                proc = run_cli(
                    [exe, "-p", prompt],
                    timeout=self.timeout_s,
                    cwd=scratch,
                    env=_scrubbed_env(_ANTIGRAVITY_SCRUB),
                )
            except subprocess.TimeoutExpired as e:
                raise EngineError(
                    f"agy CLI timed out after {self.timeout_s}s", retryable=False
                ) from e
        latency_ms = int((time.monotonic() - started) * 1000)
        if proc.returncode != 0:
            raise EngineError(
                f"agy CLI exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:2000]}"
            )
        text = proc.stdout.strip()
        if not text:
            raise EngineError(
                "agy CLI returned no output — Antigravity's non-interactive (-p) mode "
                "currently drops its response when not attached to a terminal (known "
                "upstream issue). Choose another provider until this is fixed upstream."
            )
        usage = EngineUsage(internal_calls=1, latency_ms=latency_ms, model=self.model)
        return text, usage
