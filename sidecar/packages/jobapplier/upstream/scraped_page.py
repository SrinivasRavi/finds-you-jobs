# jobapplier.upstream.scraped_page — AGPL-3.0 subtree (see LICENSE).
# SPDX-License-Identifier: AGPL-3.0-only
#
# Trimmed fork of Skyvern @ 28db09cb (skyvern/webeye/scraper/scraped_page.py).
# Kept: _replace_pua_with_marker, build_attribute, json_to_html, ElementTreeFormat,
#   ElementTreeBuilder, ScrapeFrameDecision (+ the ScrapeExcludeFunc alias the
#   carried scraper functions type against, and the ELEMENT_NODE_ATTRIBUTES /
#   _PUA_PATTERN module constants they use).
# Dropped: the ScrapedPage BaseModel and its agent/refresh machinery
#   (check_pdf_*, refresh, generate_scraped_page*, economy/lean tree builders)
#   and the CleanupElementTreeFunc alias — none reached by the observation core.
#
# Trims:
#   - structlog -> stdlib logging (see constants.get_logger()).
#   - skyvern.forge.sdk.api.crypto.calculate_sha256 -> local hashlib helper.
#   - skyvern_context: json_to_html stored long-href hashes on the request
#     context's hashed_href_map; that context is dropped, so the map is a
#     module-level dict here (same substitution behaviour, process-scoped
#     instead of request-scoped).
#   - UnknownElementTreeFormat import dropped: only the dropped ScrapedPage
#     builders raised it; no kept function references it.
"""Element-tree -> HTML rendering carried from Skyvern's scraped_page."""

from __future__ import annotations

import copy
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable

from playwright.async_api import Frame, Page

from .constants import get_logger

LOG = get_logger()


def calculate_sha256(data: str) -> str:
    """Helper function to calculate SHA256 hash of a string."""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(data.encode())
    return sha256_hash.hexdigest()


# json_to_html replaces long hrefs with a jinja-style placeholder and records the
# hash -> href mapping. Upstream stored this on the per-request skyvern_context;
# that context is trimmed, so the map lives at module scope here.
_HASHED_HREF_MAP: dict[str, str] = {}


@dataclass
class ScrapeFrameDecision:
    """Result of a scrape-frame filter callback.

    ``exclude`` drops the frame from the scraped element tree. ``placeholder``, when
    set, is a non-interactable element-tree node appended in the frame's place — a
    signal node for a frame the tree would otherwise miss (e.g. a cross-origin captcha
    living inside a closed shadow root). Placeholders are tree-only: they are never
    added to the flat interactable elements list, so they can never become a target.
    """

    exclude: bool
    placeholder: dict | None = None


ScrapeExcludeFunc = Callable[[Page, Frame], Awaitable[ScrapeFrameDecision]]

ELEMENT_NODE_ATTRIBUTES = {
    "id",
}

_PUA_PATTERN = re.compile(r"[\uE000-\uF8FF\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]+")


def _replace_pua_with_marker(text: str | None) -> str:
    if not text:
        return ""
    return _PUA_PATTERN.sub("[icon]", text)


def build_attribute(key: str, value: Any) -> str:
    if isinstance(value, bool) or isinstance(value, int):
        return f'{key}="{str(value).lower()}"'

    return f'{key}="{str(value)}"' if value else key


def json_to_html(element: dict, need_skyvern_attrs: bool = True) -> str:
    """
    if element is flagged as dropped, the html format is empty
    """
    tag = element["tagName"]
    attributes: dict[str, Any] = copy.deepcopy(element.get("attributes", {}))

    interactable = element.get("interactable", False)
    if element.get("isDropped", False):
        if not interactable:
            return ""
        else:
            LOG.debug("Element is interactable. Trimmed all attributes instead of dropping it", element=element)
            attributes = {}

    # FIXME: Theoretically, all href links with over 69(64+1+4) length could be hashed
    # but currently, just hash length>150 links to confirm the solution goes well
    if "href" in attributes and len(attributes.get("href", "")) > 150:
        href = attributes.get("href", "")
        # jinja style can't accept the variable name starts with number
        # adding "_" to make sure the variable name is valid.
        hashed_href = "_" + calculate_sha256(href)
        _HASHED_HREF_MAP[hashed_href] = href
        attributes["href"] = "{{" + hashed_href + "}}"

    if need_skyvern_attrs:
        # adding the node attribute to attributes
        for attr in ELEMENT_NODE_ATTRIBUTES:
            value = element.get(attr)
            if value is None:
                continue
            attributes[attr] = value

    attributes_html = " ".join(build_attribute(key, value) for key, value in attributes.items())

    if element.get("isSelectable", False):
        tag = "select"

    text = element.get("text", "")
    # build children HTML
    children_html = "".join(
        json_to_html(child, need_skyvern_attrs=need_skyvern_attrs) for child in element.get("children", [])
    )
    # build option HTML
    option_html = "".join(
        f'<option index="{option.get("optionIndex")}">{option.get("text")}</option>'
        if option.get("text")
        else f'<option index="{option.get("optionIndex")}" value="{option.get("value")}">{option.get("text")}</option>'
        for option in element.get("options", [])
    )

    if element.get("purgeable", False):
        return children_html + option_html

    before_pseudo_text = _replace_pua_with_marker(element.get("beforePseudoText"))
    after_pseudo_text = _replace_pua_with_marker(element.get("afterPseudoText"))

    # Check if the element is self-closing
    if (
        tag in ["img", "input", "br", "hr", "meta", "link"]
        and not option_html
        and not children_html
        and not before_pseudo_text
        and not after_pseudo_text
    ):
        return f"<{tag}{attributes_html if not attributes_html else ' ' + attributes_html}>"
    else:
        return f"<{tag}{attributes_html if not attributes_html else ' ' + attributes_html}>{before_pseudo_text}{text}{children_html + option_html}{after_pseudo_text}</{tag}>"


class ElementTreeFormat(StrEnum):
    JSON = "json"  # deprecate JSON format soon. please use HTML format
    HTML = "html"


class ElementTreeBuilder(ABC):
    @abstractmethod
    def support_economy_elements_tree(self) -> bool:
        pass

    @abstractmethod
    def support_lean_elements_tree(self) -> bool:
        """SKY-9718 Layer 1 — whether this builder implements build_lean_elements_tree.

        Mirrors `support_economy_elements_tree`. Callers of `load_prompt_with_elements`
        check this before passing lean flags so builders that only implement the
        plain `build_element_tree` (e.g. `IncrementalScrapePage`) don't crash.
        """

    @abstractmethod
    def build_element_tree(
        self, fmt: ElementTreeFormat = ElementTreeFormat.HTML, html_need_skyvern_attrs: bool = True
    ) -> str:
        pass

    @abstractmethod
    def build_economy_elements_tree(
        self,
        fmt: ElementTreeFormat = ElementTreeFormat.HTML,
        html_need_skyvern_attrs: bool = True,
        percent_to_keep: float = 1,
    ) -> str:
        pass

    @abstractmethod
    def build_lean_elements_tree(
        self,
        fmt: ElementTreeFormat = ElementTreeFormat.HTML,
        html_need_skyvern_attrs: bool = True,
        *,
        compress_long_href: bool = False,
        compress_image_src: bool = False,
        strip_url_query_strings: bool = False,
        compress_nonnavigable_href: bool = False,
    ) -> str:
        pass

    # Sanitized HTML of the last element tree built for the LLM; None when the
    # last build was JSON or none has run yet. Builders that never render HTML
    # (e.g. IncrementalScrapePage) leave it None.
    last_used_element_tree_html: str | None
