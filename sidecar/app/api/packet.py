"""Save → packet enqueue (architecture §4.2 long-op UX, AM5).

Saving a job (or a manual regenerate) enqueues the tailored-resume and
cover-letter operations as two independent ops and pre-creates one empty
`Artifact` row per op — carrying `operation_id` — so `packetState` reads
*generating* immediately. The entrypoint fills that same row on success;
regeneration chains the prior head via `superseded_by`.

The prior repository also enqueued a Save-time `prep` op here; Save-time
form-prep is retired in this rebuild (`docs/internal/applier.md` §2).
"""

from __future__ import annotations

from ..db import Database
from ..runner import OperationRunner

_ARTIFACT_KIND = {"tailor": "tailored_resume", "cover": "cover_letter"}


def auto_resume_default(thresholds: dict | None) -> bool:
    thresholds = thresholds or {}
    if "auto_resume_on_save" in thresholds:
        return bool(thresholds["auto_resume_on_save"])
    if "auto_packet_on_save" in thresholds:
        return bool(thresholds["auto_packet_on_save"])
    return True


def auto_cover_default(thresholds: dict | None) -> bool:
    thresholds = thresholds or {}
    if "auto_cover_on_save" in thresholds:
        return bool(thresholds["auto_cover_on_save"])
    if "auto_packet_on_save" in thresholds:
        return bool(thresholds["auto_packet_on_save"])
    return True


def enqueue_packet(
    db: Database,
    runner: OperationRunner,
    *,
    application_id: str,
    job_id: str,
    resume: bool,
    cover: bool,
    guidance: str = "",
) -> list[str]:
    """Enqueue the requested packet operations. Returns the operation ids."""
    with db.repos() as repos:
        profile = repos.profile.get_current()
        profile_version = profile.version if profile is not None else 1
        existing_heads = {
            a.kind: a
            for a in repos.artifacts.list_for_application(application_id)
            if a.superseded_by is None
        }
    op_ids: list[str] = []
    kinds = [k for k, want in (("tailor", resume), ("cover", cover)) if want]
    for op_kind in kinds:
        snapshot = {
            "application_id": application_id,
            "job_id": job_id,
            "guidance": guidance,
            "profile_version": profile_version,
        }
        op_id = runner.submit(op_kind, snapshot)
        op_ids.append(op_id)
        artifact_kind = _ARTIFACT_KIND[op_kind]
        with db.repos() as repos:
            artifact = repos.artifacts.create(
                application_id,
                kind=artifact_kind,
                markdown="",
                notes=[],
                profile_version=profile_version,
                guidance_used=guidance or None,
                operation_id=op_id,
            )
            prior = existing_heads.get(artifact_kind)
            if prior is not None:
                repos.artifacts.update(prior.id, superseded_by=artifact.id)
    return op_ids
