"""Shared Claude-subscription engine (ROADMAP §5 M1 playbook step 4).

One reusable way for every module (Tailorer, CoverLetterer, Scorer, parity
runners, ...) to call Claude on the maintainer's Max subscription: the
`claude` CLI in print mode, driven as a subprocess. No API key involved —
`claude` must be logged in. Model defaults to Opus 4.8 (maintainer decision
2026-07-03) and is pinned per run for reproducible parity.

Extracted from `sidecar/modules/tailorer/engine.py` at the second consumer
(the M1.2/M1.5 parity runner), exactly as the M1 replication playbook
prescribed. Module-specific engines wrap this and convert `EngineError` /
`EngineUsage` into their own typed contract.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-opus-4-8"

# Env vars that, when present in the child env, silently flip the `claude` CLI
# from subscription billing to pay-per-token API billing. The provider is named
# "Claude subscription" — cost honesty demands the subscription actually pays,
# so every subprocess here scrubs them. (Insight credited to JustHireMe's
# subscription-CLI provider — concept re-implemented, no code copied; see
# THIRD_PARTY_NOTICES.md.)
_SCRUB_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

_CLAUDE_PATH: str | None = None


def _subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _SCRUB_ENV:
        env.pop(key, None)
    return env


def resolve_claude(refresh: bool = False) -> str | None:
    """Locate the `claude` binary the way the user's terminal would.

    A GUI-launched desktop app inherits a minimal PATH (Finder on macOS gives it
    no `~/.zshrc` edits and no Homebrew shellenv), so a bare `shutil.which`
    misses a `claude` that works fine in the user's terminal — the root cause of
    onboarding's "not found" / "Load failed" verify failures. When the direct
    lookup fails, fall back to the user's login+interactive shell's PATH: the
    exact environment `claude` runs in when they type it. Cached once found;
    `refresh` forces a re-probe (e.g. the user installs the CLI, then Verifies).
    """
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
    """Login state via `claude auth status` — instant JSON metadata, no LLM call.

    Returns `{logged_in, email, plan}` (email/plan may be None), or None when
    the probe can't answer (CLI missing, an older CLI without the subcommand,
    unparseable output) — callers then fall back to a minimal real completion.
    Scrubbed env for a truthful answer about the *subscription* login.
    """
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
    """The CLI call failed; the message carries the verbatim cause."""


class ClaudeCliEngine:
    """`claude -p` subprocess on the maintainer's subscription.

    `cwd` and `extra_args` exist for callers that drive a repo-rooted agent
    (e.g. the career-ops parity runner sets cwd to the career-ops checkout and
    passes permission-mode flags). Plain completions leave both unset.
    """

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
            raise EngineError("`claude` CLI not found on PATH")
        cmd = [exe, "-p", "--model", self.model, "--output-format", "json"]
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]
        cmd += list(self.extra_args)
        started = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                input=user_prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
                cwd=self.cwd,
                env=_subscription_env(),
            )
        except subprocess.TimeoutExpired as e:
            raise EngineError(f"claude CLI timed out after {self.timeout_s}s") from e
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
