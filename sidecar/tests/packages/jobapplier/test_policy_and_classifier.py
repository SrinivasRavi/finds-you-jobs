# finds-you-jobs — AGPL-3.0-only.
"""URL policy (section 4.3) and page-state classifier (section 5.1) — pure logic."""

from datetime import UTC, datetime

import pytest

from sidecar.packages.jobapplier.classifier import classify
from sidecar.packages.jobapplier.executor import UrlPolicy
from sidecar.packages.jobapplier.observe import Observation, ObservedElement
from sidecar.packages.jobapplier.types import PageState

# ---------------------------------------------------------------------------
# UrlPolicy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/acme/jobs/1",
        "http://careers.example.com/apply",
    ],
)
def test_policy_allows_public_http(url: str) -> None:
    assert UrlPolicy().check(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://example.com/x",
        "http://127.0.0.1:8843/api",  # the sidecar itself (section 4.3)
        "http://localhost/admin",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata endpoint
        "http://0.0.0.0/",
        "https://",
    ],
)
def test_policy_rejects_local_private_and_odd_schemes(url: str) -> None:
    assert UrlPolicy().check(url) is not None


def test_policy_allow_local_is_for_fixtures_only() -> None:
    assert UrlPolicy(allow_local=True).check("file:///tmp/fixture.html") is None
    assert UrlPolicy(allow_local=True).check("http://127.0.0.1:1420/x") is None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def _el(
    eid: str,
    tag: str,
    label: str = "",
    text: str = "",
    attrs: dict[str, str] | None = None,
) -> ObservedElement:
    return ObservedElement(
        element_id=eid,
        unique_id=f"u-{eid}",
        tag=tag,
        label=label,
        text=text,
        value="",
        attributes=attrs or {},
        interactable=True,
        frame_index=0,
    )


def _obs(
    elements: list[ObservedElement],
    html: str,
    title: str = "Acme — Staff Engineer",
) -> Observation:
    return Observation(
        url="https://careers.example.com/jobs/1",
        title=title,
        screenshot_png=b"\x89PNG\r\n\x1a\n",
        elements=tuple(elements),
        element_tree_html=html,
        frame_count=1,
        captured_at=datetime.now(UTC),
    )


def test_application_form_detected() -> None:
    obs = _obs(
        [
            _el("e1", "input", label="Full name"),
            _el("e2", "input", label="Email", attrs={"type": "email"}),
            _el("e3", "input", label="Resume", attrs={"type": "file"}),
            _el("e4", "textarea", label="Why do you want to work here?"),
        ],
        "<form>Full name Email Resume</form>",
    )
    assert PageState.APPLICATION_FORM in classify(obs)


def test_newsletter_form_is_not_an_application_form() -> None:
    # section 5.1: three generic fields + newsletter vocabulary is not an
    # application form.
    obs = _obs(
        [
            _el("e1", "input", label="Your email"),
            _el("e2", "input", label="Name"),
            _el("e3", "input", label="Country"),
        ],
        "<div>Join our talent community — subscribe to job alerts</div>",
    )
    assert PageState.APPLICATION_FORM not in classify(obs)


def test_apply_button_and_external_link() -> None:
    obs = _obs(
        [
            _el("e1", "button", label="Apply now"),
            _el(
                "e2",
                "a",
                text="Apply for this job",
                attrs={"href": "https://jobs.lever.co/acme/123"},
            ),
        ],
        "<h1>Staff Engineer</h1><p>Responsibilities and qualifications</p>",
    )
    states = classify(obs)
    assert PageState.APPLY_LINK_OR_BUTTON in states
    assert PageState.EXTERNAL_APPLICATION_LINK in states
    assert PageState.JOB_DESCRIPTION in states


def test_login_wall_needs_a_password_field() -> None:
    with_pw = _obs(
        [_el("e1", "input", label="Password", attrs={"type": "password"})],
        "<div>Sign in to continue</div>",
    )
    assert PageState.LOGIN_WALL in classify(with_pw)
    # "sign in" text alone (a nav link) is not a wall.
    without_pw = _obs(
        [_el("e1", "a", text="Sign in")], "<div>Sign in</div>"
    )
    assert PageState.LOGIN_WALL not in classify(without_pw)


@pytest.mark.parametrize(
    ("html", "state"),
    [
        ("<div>This position has been filled.</div>", PageState.POSTING_CLOSED),
        ("<div>Thank you for applying — application received.</div>", PageState.CONFIRMATION),
        ("<div>Email is required</div>", PageState.VALIDATION_ERROR),
    ],
)
def test_text_signal_states(html: str, state: PageState) -> None:
    assert state in classify(_obs([], html))


def test_captcha_needs_a_rendered_challenge() -> None:
    # A passive reCAPTCHA v3 badge (the word in the markup, no interactive
    # challenge) must NOT wall the run — that false positive aborted 81% of
    # measured forms. The word alone is not enough.
    passive_badge = _obs(
        [_el("e1", "div", text="protected by reCAPTCHA — Privacy · Terms")],
        "<div>This site is protected by reCAPTCHA and Cloudflare.</div>",
    )
    assert PageState.CAPTCHA_OR_ANTI_BOT not in classify(passive_badge)

    invisible_anchor = _obs(
        [
            _el(
                "e1",
                "iframe",
                attrs={"src": "https://www.google.com/recaptcha/api2/anchor?size=invisible"},
            )
        ],
        "<div>protected by reCAPTCHA</div>",
    )
    assert PageState.CAPTCHA_OR_ANTI_BOT not in classify(invisible_anchor)


@pytest.mark.parametrize(
    "obs",
    [
        # A reCAPTCHA challenge iframe (the bframe popup), not the badge.
        _obs(
            [
                _el(
                    "e1",
                    "iframe",
                    attrs={"src": "https://www.google.com/recaptcha/api2/bframe?hl=en"},
                )
            ],
            "<div>reCAPTCHA</div>",
        ),
        # An interactive "I'm not a robot" control.
        _obs(
            [_el("e1", "div", text="I'm not a robot", attrs={"role": "checkbox"})],
            "<div>reCAPTCHA verification</div>",
        ),
        # A Cloudflare interstitial page (title carries the signal).
        _obs(
            [],
            "<div>Checking your browser before accessing. Cloudflare</div>",
            title="Just a moment...",
        ),
    ],
)
def test_captcha_detected_on_a_real_challenge(obs: Observation) -> None:
    assert PageState.CAPTCHA_OR_ANTI_BOT in classify(obs)


def test_unknown_when_nothing_matches() -> None:
    assert classify(_obs([], "<div>hello</div>")) == frozenset({PageState.UNKNOWN})
