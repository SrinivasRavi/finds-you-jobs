# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""Page-state classifier (docs/internal/applier.md §5.1).

Pure logic over an ``Observation``: no browser, no network, no model call —
so it is exhaustively unit-testable. It must not declare an application form
because a page has three generic text fields; a newsletter/talent-community
form is not an application form. The classifier returns *every* matching
state; the loop decides what to do with the set.
"""

from __future__ import annotations

import re

from .observe import Observation
from .types import PageState

# Signal vocabularies. Deliberately conservative: a miss degrades to UNKNOWN
# (the agent keeps observing) — a false APPLICATION_FORM would make the agent
# fill the wrong surface, which is worse.
_APPLY_CONTROL = re.compile(
    r"\b(apply now|apply for this job|apply to this|submit application|"
    r"apply today|easy apply|start application|apply)\b",
    re.IGNORECASE,
)
# Application-SPECIFIC vocabulary only. Generic contact fields (name, email,
# phone) deliberately do NOT count — a newsletter/talent-community signup has
# those too (§5.1); what distinguishes an application form is resume/CV,
# authorization, salary, screening-question language, or a file upload.
_FORM_FIELD_HINTS = re.compile(
    r"\b(resume|cv|cover letter|linkedin( profile)?|work authorization|"
    r"sponsorship|salary|how did you hear|why do you want|"
    r"years of experience|notice period)\b",
    re.IGNORECASE,
)
_NEWSLETTER_HINTS = re.compile(
    r"\b(newsletter|talent (community|network|pool)|job alerts?|"
    r"subscribe|stay (in touch|connected)|get notified)\b",
    re.IGNORECASE,
)
# Bare "password" is deliberately absent: an application form may legitimately
# contain a password-typed field (portal accounts) without being a login wall.
# The wall needs sign-in VOCABULARY *and* a password field (see classify).
_LOGIN_HINTS = re.compile(
    r"\b(sign in|log ?in|create (an )?account|forgot password|"
    r"continue with (google|linkedin|apple))\b",
    re.IGNORECASE,
)
_CAPTCHA_HINTS = re.compile(
    r"\b(captcha|recaptcha|hcaptcha|are you a robot|unusual traffic|"
    r"verify you are human|cloudflare)\b",
    re.IGNORECASE,
)
_CLOSED_HINTS = re.compile(
    r"\b(no longer (accepting|available|active)|position (has been )?(filled|closed)|"
    r"job (is )?closed|posting (has )?expired|this job is not available)\b",
    re.IGNORECASE,
)
_CONFIRMATION_HINTS = re.compile(
    r"\b(application (received|submitted|complete)|thank you for (applying|your application)|"
    r"we('ve| have) received your application|successfully submitted)\b",
    re.IGNORECASE,
)
_VALIDATION_HINTS = re.compile(
    r"\b((is|are) required|required field|please (fill|enter|select|complete)|"
    r"invalid (email|phone|value)|must (be|contain|enter))\b",
    re.IGNORECASE,
)
_JD_HINTS = re.compile(
    r"\b(responsibilities|qualifications|requirements|what you.ll do|"
    r"about (the|this) role|benefits|who you are|job description)\b",
    re.IGNORECASE,
)

_INPUT_TAGS = {"input", "textarea", "select"}


def classify(obs: Observation) -> frozenset[PageState]:
    """Every state the observation matches; ``{UNKNOWN}`` when nothing does."""
    text = obs.element_tree_html
    states: set[PageState] = set()

    input_labels = [
        f"{e.label} {e.text}".strip()
        for e in obs.elements
        if e.tag.lower() in _INPUT_TAGS
    ]
    n_inputs = len(input_labels)
    labels_blob = " | ".join(input_labels)

    file_upload = any(
        e.tag.lower() == "input" and e.attributes.get("type", "").lower() == "file"
        for e in obs.elements
    )
    # An application form needs application-shaped evidence: several inputs AND
    # (application field vocabulary OR a resume upload) AND not a
    # newsletter-only surface (§5.1).
    application_vocab = bool(_FORM_FIELD_HINTS.search(labels_blob) or file_upload)
    newsletter_only = bool(
        _NEWSLETTER_HINTS.search(text)
        and not file_upload
        and not _FORM_FIELD_HINTS.search(labels_blob)
    )
    if n_inputs >= 3 and application_vocab and not newsletter_only:
        states.add(PageState.APPLICATION_FORM)

    apply_controls = [
        e
        for e in obs.elements
        if e.tag.lower() in {"a", "button"} and _APPLY_CONTROL.search(f"{e.label} {e.text}")
    ]
    if apply_controls:
        states.add(PageState.APPLY_LINK_OR_BUTTON)
        if any(
            e.tag.lower() == "a"
            and _is_external(obs.url, e.attributes.get("href", ""))
            for e in apply_controls
        ):
            states.add(PageState.EXTERNAL_APPLICATION_LINK)

    if _LOGIN_HINTS.search(text) and any(
        e.attributes.get("type", "").lower() == "password" for e in obs.elements
    ):
        states.add(PageState.LOGIN_WALL)
    if _CAPTCHA_HINTS.search(text):
        states.add(PageState.CAPTCHA_OR_ANTI_BOT)
    if _CLOSED_HINTS.search(text):
        states.add(PageState.POSTING_CLOSED)
    if _CONFIRMATION_HINTS.search(text):
        states.add(PageState.CONFIRMATION)
    if _VALIDATION_HINTS.search(text):
        states.add(PageState.VALIDATION_ERROR)
    if _JD_HINTS.search(text):
        states.add(PageState.JOB_DESCRIPTION)

    return frozenset(states) if states else frozenset({PageState.UNKNOWN})


def _is_external(current_url: str, href: str) -> bool:
    """True when ``href`` leaves the current host (a hosted-ATS hop, §5.2)."""
    from urllib.parse import urlparse

    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return False
    target = urlparse(href)
    if not target.netloc:
        return False
    return target.netloc != urlparse(current_url).netloc
