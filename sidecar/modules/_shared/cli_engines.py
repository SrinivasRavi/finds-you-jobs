"""Subscription-CLI engine family — Codex (ChatGPT plan) and Antigravity
(Google AI plan), beside the shared Claude engine (`claude_engine.py`).

One pattern, three CLIs: the user's already-logged-in coding CLI is driven as a
print-mode subprocess, so no API key ever enters this app and the subscription
pays. Each engine implements the same duck-typed contract as `ClaudeCliEngine`
(`complete(system, user) -> (text, EngineUsage)`) and reuses its `EngineUsage`
/ `EngineError` types.

Containment (2026-07-17 design decision, discovery/engines expansion): unlike
`claude -p`, `codex exec` and `agy -p` are *agents* that may execute tool
calls. Every call here therefore (a) runs in a fresh empty scratch directory,
never the app or user cwd, and (b) for Codex passes `--sandbox read-only`. A
prompt-injected job description must not be able to touch the user's files.
Antigravity's `-p` mode auto-approves tool calls with no known restriction
flag — one of the reasons that provider ships labeled **experimental**.

Billing scrub mirrors `claude_engine._subscription_env`: env vars that would
silently flip the CLI from subscription auth to pay-per-token API billing are
removed from the child env (cost honesty — the tile says "subscription").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

from .claude_engine import EngineError, EngineUsage

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
    """`codex exec` on the user's ChatGPT subscription.

    - `--json`: JSONL event stream; the final agent message and the
      `turn.completed` usage record are parsed out of it. An unparseable
      stream degrades to plain-text stdout (older CLI), like ClaudeCliEngine.
    - `--sandbox read-only` + empty scratch cwd: containment (module docstring).
    - `model=None` omits `-m` — the CLI's own configured default model runs,
      which is the honest choice for a subscription (the user picked it there).
    - The system prompt is prepended to the prompt text: `codex exec` has no
      append-system-prompt flag; its input is simply "the task".
    """

    def __init__(self, model: str | None = None, timeout_s: int = 600) -> None:
        self.model = model or None
        self.timeout_s = timeout_s

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        exe = resolve_cli("codex")
        if exe is None:
            raise EngineError("`codex` CLI not found on PATH")
        cmd = [exe, "exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check"]
        if self.model:
            cmd += ["--model", self.model]
        cmd += ["-"]  # prompt on stdin — argv has size limits, resumes+JDs don't
        prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="fyj-codex-") as scratch:
            try:
                proc = subprocess.run(  # noqa: S603
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=False,
                    cwd=scratch,
                    env=_scrubbed_env(_CODEX_SCRUB),
                )
            except subprocess.TimeoutExpired as e:
                raise EngineError(f"codex CLI timed out after {self.timeout_s}s") from e
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
    """`agy -p` on the user's Google AI (Antigravity) subscription. Experimental.

    Known upstream limits, current as of 2026-07 (all handled honestly, none
    hidden): `--output-format json` is not stable, so plain-text stdout is the
    contract (no token usage available — the ledger records calls + latency
    only); `-p` under a non-TTY can drop the final response entirely, which
    surfaces here as a clear EngineError rather than a silent empty result —
    and the Verify flow runs a real completion through this exact path so the
    breakage is caught at setup, not mid-pipeline; `-p` auto-approves tool
    calls, hence the empty scratch cwd (see module docstring).

    No model flag: the account's Antigravity default model runs.
    """

    def __init__(self, model: str | None = None, timeout_s: int = 600) -> None:
        self.model = model or None  # accepted for the factory contract; unused
        self.timeout_s = timeout_s

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        exe = resolve_cli("agy")
        if exe is None:
            raise EngineError("`agy` (Antigravity CLI) not found on PATH")
        prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="fyj-agy-") as scratch:
            try:
                proc = subprocess.run(  # noqa: S603
                    [exe, "-p", prompt],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=False,
                    cwd=scratch,
                    env=_scrubbed_env(_ANTIGRAVITY_SCRUB),
                )
            except subprocess.TimeoutExpired as e:
                raise EngineError(f"agy CLI timed out after {self.timeout_s}s") from e
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
