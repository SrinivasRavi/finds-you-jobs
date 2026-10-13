"""Applicant details (audit A5, maintainer 2026-10-13): confirmed once before the
first apply, several work authorizations for the model to choose from, the
posting's location on every form, and a resume read that keeps what was typed."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.registry.applicant import (
    apply_facts,
    details_confirmed,
    merge_extracted,
    missing_required,
    work_authorization_line,
)

from ._support import AUTH, TOKEN

RECORD = {
    "first_name": "Asha",
    "last_name": "Rao",
    "email": "asha@example.com",
    "phone": "+91 98450 00000",
    "location": "Bengaluru, Karnataka, India",
    "country": "India",
    "links": ["https://github.com/asha"],
    "work_authorizations": [
        {"country": "India", "status": "citizen"},
        {"country": "United States", "status": "needs_sponsorship", "note": "F-1 OPT ends 2027"},
    ],
    "confirmed_at": "2026-10-13T00:00:00+00:00",
    "source": "edited",
}


def test_the_form_gets_the_postings_location_not_the_users() -> None:
    facts = apply_facts(RECORD, "London, ON")
    assert facts["location"] == "London, Ontario, Canada"
    assert facts["country"] == "Canada"
    assert facts["first name"] == "Asha"
    # Bookkeeping never reaches the model.
    assert not {"source", "confirmed_at", "work_authorizations"} & set(facts)


def test_only_a_name_and_a_location_are_required() -> None:
    assert missing_required(RECORD) == []
    assert missing_required({"name": "Asha", "location": "Pune"}) == []
    assert missing_required({"first_name": " ", "location": ""}) == ["name", "location"]
    assert missing_required(None) == ["name", "location"]


def test_the_optional_fields_reach_the_model_by_their_names() -> None:
    record = {**RECORD, "salary_expectation": "INR 4,000,000 / year", "notice_period": "30 days",
              "years_of_experience": 9, "citizenship": "India",
              "veteran_status": "no", "disability_status": "yes", "gender": "Woman"}
    facts = apply_facts(record, "Remote")
    assert facts["salary expectation"] == "INR 4,000,000 / year"
    assert facts["notice period"] == "30 days"
    assert facts["years of experience"] == "9"
    assert facts["citizenship"] == "India"
    assert facts["veteran status"] == "not a protected veteran"
    assert facts["disability status"] == "has a disability"
    assert facts["gender"] == "Woman"


def test_an_unset_self_identification_answer_is_a_decline() -> None:
    facts = apply_facts({**RECORD, "veteran_status": "decline"}, "Remote")
    for key in ("gender", "race ethnicity", "veteran status", "disability status"):
        assert facts[key].startswith("I don't wish to answer")
    # Anything else unset is simply absent: the Applier leaves it for review.
    assert "salary expectation" not in facts


def test_a_posting_with_no_place_keeps_the_users_own_location() -> None:
    facts = apply_facts(RECORD, "Remote")
    assert facts["location"] == "Bengaluru, Karnataka, India"
    assert facts["country"] == "India"


def test_every_authorization_reaches_the_model_with_how_to_choose() -> None:
    line = apply_facts(RECORD, "Seattle, WA")["work authorization"]
    assert "India: citizen, no sponsorship needed" in line
    assert "United States: not authorized yet, needs visa sponsorship (F-1 OPT ends 2027)" in line
    assert "for the country the job is in" in line


def test_an_old_free_text_authorization_still_reaches_the_model() -> None:
    assert work_authorization_line({"work_authorization": "Authorized in India"}) == (
        "Authorized in India"
    )
    assert work_authorization_line({}) == ""


def test_a_resume_read_never_overwrites_what_the_user_saved() -> None:
    extracted = {"first_name": "A.", "email": "", "phone": "+91 1", "location": "Pune",
                 "work_authorization": "", "source": "extracted", "profile_version": 3}
    merged = merge_extracted(RECORD, extracted)
    assert merged["first_name"] == "Asha"  # typed wins
    assert merged["email"] == "asha@example.com"
    assert merged["work_authorizations"] == RECORD["work_authorizations"]
    assert details_confirmed(merged)
    assert merged["profile_version"] == 3
    # Before any save, the read is the record.
    assert merge_extracted(None, extracted) == extracted
    unconfirmed = merge_extracted({"first_name": "Old"}, extracted)
    assert unconfirmed["first_name"] == "A."


def _seed_job(client: TestClient, app: Any) -> str:
    client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# Asha\n\nEngineer."})
    job = client.post(
        "/api/jobs", headers=AUTH,
        json={"canonical_url": "https://example.com/apply-me", "title": "Engineer",
              "company": "Acme", "location": "London, ON", "description": "x" * 300,
              "source_adapter": "paste-url"},
    ).json()
    card = client.post("/api/applications", headers=AUTH,
                       json={"job_id": job["id"], "generate_resume": False,
                             "generate_cover": False}).json()
    return str(card["id"])


def test_apply_waits_for_confirmed_details_and_saving_them_is_the_confirmation(
    tmp_path,
) -> None:
    app = create_app(token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
                     enable_scheduler=False)
    with TestClient(app) as client:
        card_id = _seed_job(client, app)
        # Read from the resume, never reviewed: still refused.
        with app.state.db.repos() as repos:
            repos.profile.set_application_profile({"first_name": "Asha", "source": "extracted"})
        r = client.post(f"/api/applications/{card_id}/apply", headers=AUTH, json={})
        assert r.status_code == 409
        assert "applicant details" in r.json()["detail"]
        saved = client.patch(
            "/api/profile/application-profile", headers=AUTH,
            json={k: v for k, v in RECORD.items() if k not in ("confirmed_at", "source")},
        ).json()
        assert saved["application_profile"]["confirmed_at"]
        assert saved["application_profile"]["work_authorizations"][1]["country"] == (
            "United States"
        )
        r = client.post(f"/api/applications/{card_id}/apply", headers=AUTH, json={})
        assert r.status_code != 409, r.text


def test_saving_without_a_name_or_location_is_refused(tmp_path) -> None:
    app = create_app(token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
                     enable_scheduler=False)
    with TestClient(app) as client:
        client.post("/api/profile", headers=AUTH, json={"resume_markdown": "# Asha"})
        r = client.patch("/api/profile/application-profile", headers=AUTH,
                         json={"first_name": "Asha", "email": "a@example.com"})
        assert r.status_code == 422
        assert "location" in r.json()["detail"]
        r = client.patch("/api/profile/application-profile", headers=AUTH,
                         json={"first_name": "Asha", "location": "Pune"})
        assert r.status_code == 200


def test_the_countries_list_is_named_and_sorted(tmp_path) -> None:
    app = create_app(token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
                     enable_scheduler=False)
    with TestClient(app) as client:
        rows = client.get("/api/geo/countries", headers=AUTH).json()
    names = [r["name"] for r in rows]
    assert names == sorted(names)
    assert {"code": "IN", "name": "India"} in rows
    assert len(rows) > 200
