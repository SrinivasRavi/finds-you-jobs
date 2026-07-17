# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""Typed observation seam over the Skyvern-derived core (docs/internal/applier.md §4.1).

One ``observe(page)`` call produces one immutable ``Observation``: a viewport
screenshot, a compact interactive-element tree rendered to HTML, and a flat list
of ``ObservedElement``s carrying a fresh opaque id (``e1``, ``e2`` …) for the
current observation only.

Element ids are per-observation and go stale the instant the next ``observe``
runs (or the page navigates/rerenders). An action must reference an id from the
CURRENT observation; the executor rejects stale references rather than guessing
(§4.1). ``unique_id`` is the underlying Skyvern per-scan DOM id the executor
resolves against; ``element_id`` is the opaque handle the model sees.

Observation is frame-aware: the tree is walked across the main frame and its
same-origin child frames, and each element carries the ``frame_index`` it was
found in (main frame is 0, child frames follow ``page.frames`` order).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import Page

from .upstream.constants import SKYVERN_ID_ATTR
from .upstream.page_utils import _current_viewpoint_screenshot_helper
from .upstream.scraped_page import json_to_html
from .upstream.scraper import get_interactable_element_tree, trim_element_tree


@dataclass(frozen=True)
class ObservedElement:
    """One interactive element, as seen in the current observation.

    ``element_id`` is the opaque per-observation handle (``e1`` …); ``unique_id``
    is the Skyvern per-scan DOM id the executor resolves against. Both expire
    when the next observation is taken.
    """

    element_id: str
    unique_id: str
    tag: str
    interactable: bool
    frame_index: int
    role: str | None = None
    label: str = ""
    text: str = ""
    value: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """An immutable snapshot of the page for one model turn (§4.1)."""

    url: str
    title: str
    screenshot_png: bytes
    elements: tuple[ObservedElement, ...]
    element_tree_html: str
    frame_count: int
    captured_at: datetime


def _walk(nodes: list[dict]) -> Iterator[dict]:
    """Depth-first preorder walk over the untrimmed tree.

    Preorder (which still carries ``id`` and ``frame_index``) is what makes the
    ``e1..eN`` numbering stable and readable: it mirrors document order across
    the main frame and its child-frame subtrees.
    """
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def _build_label_map(element_tree: list[dict]) -> dict[str, str]:
    """Map an ``id`` -> its associated ``<label for=...>`` text.

    Skyvern surfaces the ``<label>`` and its control as separate elements; the
    flat element list the model sees needs the human-readable label carried ON
    the control. Only explicit ``for``/``id`` association is resolved (what the
    fixtures and typical ATS forms use)."""
    label_map: dict[str, str] = {}
    for node in _walk(element_tree):
        if node.get("tagName") != "label":
            continue
        target = (node.get("attributes") or {}).get("for")
        text = str(node.get("text", "") or "").strip()
        if target and text:
            label_map[str(target)] = text
    return label_map


def _to_observed(node: dict, element_id: str, associated_label: str) -> ObservedElement:
    raw_attributes: dict[str, Any] = node.get("attributes", {}) or {}
    # The Skyvern id marker is exposed as ``unique_id`` on the dataclass, not as
    # a raw attribute the model reasons about.
    attributes = {key: value for key, value in raw_attributes.items() if key != SKYVERN_ID_ATTR}
    role = raw_attributes.get("role") or raw_attributes.get("aria-role")
    # An explicit <label for=...> is the most authoritative visible label;
    # otherwise fall back to the element's own labelling attributes.
    label = (
        associated_label
        or raw_attributes.get("aria-label")
        or raw_attributes.get("title")
        or raw_attributes.get("placeholder")
        or raw_attributes.get("name")
        or ""
    )
    return ObservedElement(
        element_id=element_id,
        unique_id=str(node["id"]),
        tag=str(node.get("tagName", "")),
        interactable=bool(node.get("interactable", False)),
        frame_index=int(node.get("frame_index", 0)),
        role=str(role) if role is not None else None,
        label=str(label),
        text=str(node.get("text", "") or ""),
        value=raw_attributes.get("value"),
        attributes=attributes,
    )


async def observe(page: Page) -> Observation:
    """Observe ``page`` once: screenshot + interactive-element tree + frame walk.

    The returned ``Observation`` is self-consistent for exactly this instant. Its
    ``element_id``s are reassigned from scratch on the next ``observe`` — never
    reuse an id across observations.
    """
    elements, element_tree = await get_interactable_element_tree(page)

    surfaced_ids = {element["id"] for element in elements if element.get("id")}
    label_map = _build_label_map(element_tree)

    observed: list[ObservedElement] = []
    for node in _walk(element_tree):
        node_id = node.get("id")
        if not node_id or node_id not in surfaced_ids:
            continue
        attributes = node.get("attributes") or {}
        # A <label for=...> is now carried on its target control; drop the
        # standalone label row so the flat list addresses the control directly.
        if node.get("tagName") == "label" and attributes.get("for"):
            continue
        associated_label = label_map.get(str(attributes.get("id", "")), "")
        observed.append(_to_observed(node, f"e{len(observed) + 1}", associated_label))

    # Trim a deep copy for rendering so the untrimmed tree stays intact for the
    # element walk above; render compact HTML without the internal id markers.
    trimmed = trim_element_tree(copy.deepcopy(element_tree))
    element_tree_html = "".join(json_to_html(node, need_skyvern_attrs=False) for node in trimmed)

    # Distinct frames that contributed elements, plus the main frame (index 0).
    frame_count = len({element.get("frame_index", 0) for element in elements} | {0})

    screenshot_png = await _current_viewpoint_screenshot_helper(page)

    return Observation(
        url=page.url,
        title=await page.title(),
        screenshot_png=screenshot_png,
        elements=tuple(observed),
        element_tree_html=element_tree_html,
        frame_count=frame_count,
        captured_at=datetime.now(UTC),
    )
