"""Deleting a job with FK children must not IntegrityError.

Same bug class as the 2026-07-24 application cascade fix (e6413a9);
this time on the `jobs` table. Without the cascade, `evict_stale_trash`,
`age_expired_jobs`, `tombstone_job`, and `empty_trash` all crash.
"""

from __future__ import annotations

from datetime import timedelta

from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.registry.persistence import delete_job_cascade, evict_stale_trash


def test_evict_stale_trash_with_referral_candidate(migrated_db: Database) -> None:
    """A trashed job with a referral candidate must not IntegrityError."""
    db = migrated_db
    now = now_utc()
    with db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="u-cascade-1",
            title="Test Job",
            source_adapter="lever",
        )
        repos.jobs.update(
            job.id,
            feed_state="removed",
            trashed_at=now - timedelta(days=10),
        )
        contact = repos.contacts.create(
            name="Alice", linkedin_url="https://linkedin.com/in/alice-cascade"
        )
        repos.referral_candidates.upsert(contact.id, job.id)
        repos.outreach_logs.create(
            contact.id, job_id=job.id, channel="dm", outcome="sent"
        )
        job_id = job.id

    # This used to raise IntegrityError: FOREIGN KEY constraint failed
    tombstoned = evict_stale_trash(db, ttl_days=7, now=now)
    assert job_id in tombstoned

    with db.repos() as repos:
        assert repos.jobs.get(job_id) is None
        assert repos.referral_candidates.list_for_job(job_id) == []


def test_delete_job_cascade_nullifies_outreach_logs(migrated_db: Database) -> None:
    """Outreach logs are audit data — cascade nullifies job_id, not deletes."""
    db = migrated_db
    with db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="u-cascade-2",
            title="Test Job 2",
            source_adapter="lever",
        )
        contact = repos.contacts.create(
            name="Bob", linkedin_url="https://linkedin.com/in/bob-cascade"
        )
        log = repos.outreach_logs.create(
            contact.id, job_id=job.id, channel="dm", outcome="sent"
        )
        job_id, log_id = job.id, log.id

    with db.repos() as repos:
        delete_job_cascade(repos, job_id)

    with db.repos() as repos:
        assert repos.jobs.get(job_id) is None
        surviving_log = repos.outreach_logs.get(log_id)
        assert surviving_log is not None
        assert surviving_log.job_id is None  # nullified, not deleted
