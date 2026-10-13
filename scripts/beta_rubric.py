"""The beta rubric's free half (backlog S-G8): latency, tokens and money per
operation kind, read from the operations ledger, plus a labelling sheet for
the half that costs a human: correctness.

    uv run python scripts/beta_rubric.py [--db PATH] [--since 2026-10-01] [--sheet out.csv]

Read-only: the database is opened with mode=ro and never written. The sheet
has 1 row per finished discovery, scoring, tailoring or Applier run, with
empty `correct` and `notes` columns for the tester to fill in.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

KINDS = ("scan", "score", "tailor", "cover", "apply")


def default_db() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path.home() / ".local" / "share"
    return base / "finds-you-jobs" / "db.sqlite"


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def load(db: Path, since: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT o.id, o.kind, o.state, o.engine, o.model, o.started_at, o.finished_at,
                   o.usage, o.error, j.title, j.company
            FROM operations o LEFT JOIN jobs j ON j.id = o.job_id
            WHERE o.kind IN (?, ?, ?, ?, ?) AND o.finished_at IS NOT NULL AND o.created_at >= ?
            ORDER BY o.finished_at""",
        (*KINDS, since),
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        usage = json.loads(r["usage"]) if r["usage"] else {}
        out.append({
            **{k: r[k] for k in ("id", "kind", "state", "engine", "model", "error")},
            "title": r["title"],
            "company": r["company"],
            "finished_at": r["finished_at"],
            "latency_s": _seconds(r["started_at"], r["finished_at"]),
            "tokens": (usage.get("tokens_in") or 0) + (usage.get("tokens_out") or 0),
            "usd": usage.get("usd"),
        })
    return out


def summarize(runs: list[dict]) -> str:
    head = ("kind", "runs", "failed", "p50 s", "p90 s", "tokens", "usd")
    lines = ["{:<7} {:>5} {:>7} {:>7} {:>7} {:>10} {:>9}".format(*head)]
    for kind in KINDS:
        rs = [r for r in runs if r["kind"] == kind]
        if not rs:
            continue
        lat = [r["latency_s"] for r in rs if r["latency_s"] is not None]
        failed = sum(r["state"] != "succeeded" for r in rs)
        usd = sum(r["usd"] or 0 for r in rs)
        lines.append(
            f"{kind:<7} {len(rs):>5} {failed:>7} "
            f"{statistics.median(lat) if lat else 0:>7.1f} {_pct(lat, 0.9) if lat else 0:>7.1f} "
            f"{sum(r['tokens'] for r in rs):>10} {usd:>9.4f}"
        )
    return "\n".join(lines)


def write_sheet(runs: list[dict], path: Path) -> None:
    cols = ["id", "kind", "finished_at", "company", "title", "engine", "model", "state",
            "latency_s", "tokens", "usd", "error", "correct", "notes"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in runs:
            w.writerow({**r, "correct": "", "notes": ""})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=default_db())
    ap.add_argument("--since", default="2000-01-01")
    ap.add_argument("--sheet", type=Path)
    args = ap.parse_args(argv)
    runs = load(args.db, args.since)
    print(summarize(runs))
    if args.sheet:
        write_sheet(runs, args.sheet)
        print(f"\nwrote {len(runs)} rows to {args.sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
