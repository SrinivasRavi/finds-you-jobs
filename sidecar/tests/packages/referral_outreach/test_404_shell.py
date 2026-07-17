# voyager_py/tests — GPL v3 (see LICENSE). Inside the subtree on purpose.
"""The ephemeral-404-shell guard (_reload_past_404_shell) — observed live
2026-07-08: LinkedIn served its 404 shell at a valid /in/ URL, so every
Connect probe 'found nothing'. The guard reloads past it or raises typed."""

import pytest

from sidecar.packages.referral_outreach.upstream.actions import (
    _page_is_404_shell,
    _reload_past_404_shell,
)
from sidecar.packages.referral_outreach.upstream.errors import SkipProfile

PROFILE_HTML = "<html><title>Misha | LinkedIn</title><main>profile</main></html>"
SHELL_HTML = "<html><title></title><h1>This page doesn’t exist</h1></html>"


class StubPage:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.reloads = 0

    def content(self) -> str:
        return self._contents[0]

    def reload(self, **_kw) -> None:
        self.reloads += 1
        if len(self._contents) > 1:
            self._contents.pop(0)


class StubSession:
    def __init__(self, page: StubPage) -> None:
        self.page = page

    def wait(self, *_a, **_k) -> None:
        pass


def test_clean_page_no_reload() -> None:
    page = StubPage([PROFILE_HTML])
    _reload_past_404_shell(StubSession(page), "someone")  # type: ignore[arg-type]
    assert page.reloads == 0


def test_shell_then_profile_recovers_after_one_reload() -> None:
    page = StubPage([SHELL_HTML, PROFILE_HTML])
    _reload_past_404_shell(StubSession(page), "someone")  # type: ignore[arg-type]
    assert page.reloads == 1


def test_persistent_shell_raises_typed_skip() -> None:
    page = StubPage([SHELL_HTML])
    with pytest.raises(SkipProfile, match="404 shell"):
        _reload_past_404_shell(StubSession(page), "someone")  # type: ignore[arg-type]
    assert page.reloads == 2  # bounded


def test_marker_detection_both_apostrophes() -> None:
    assert _page_is_404_shell(StubPage(["This page doesn't exist"]))
    assert _page_is_404_shell(StubPage(["This page doesn’t exist"]))
    assert not _page_is_404_shell(StubPage([PROFILE_HTML]))
