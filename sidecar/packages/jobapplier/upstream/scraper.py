# jobapplier.upstream.scraper — AGPL-3.0 subtree (see LICENSE).
# SPDX-License-Identifier: AGPL-3.0-only
#
# Trimmed fork of Skyvern @ 28db09cb (skyvern/webeye/scraper/scraper.py).
# Kept: load_js_script, _reserved_attributes_for_context, build_element_dict,
#   clean_element_before_hashing, hash_element, get_frame_text,
#   get_all_children_frames, filter_frames, add_frame_interactable_elements,
#   get_interactable_element_tree, _should_keep_unique_id, trim_element,
#   trim_element_tree, _trimmed_base64_data, _trimmed_attributes,
#   _remove_unique_id, _build_element_links.
# Dropped: build_scraping_failed_reason, scrape_website, scrape_web_unsafe,
#   _wait_for_scrape_ready / _should_use_page_ready_wait, _record_scrape_span_attrs,
#   IncrementalScrapePage — the agent-loop / incremental / screenshot-orchestration
#   paths that are out of scope for the observation core.
#
# Trims:
#   - structlog -> stdlib logging (see constants.get_logger()).
#   - OpenTelemetry (otel_trace, @traced, span attrs) -> removed.
#   - skyvern.forge.sdk.api.crypto.calculate_sha256 -> local hashlib helper.
#   - skyvern_context removed: _reserved_attributes_for_context drops the enriched
#     context switch and returns the non-enriched default (RESERVED_ATTRIBUTES);
#     get_interactable_element_tree's persistent context.frame_index_map becomes a
#     per-call local dict (identical frame numbering within one observation).
#   - add_frame_interactable_elements no longer calls the dropped
#     _wait_for_scrape_ready readiness wait.
#   - load_js_script repointed to this package's domUtils.js via __file__.
#   - DEFAULT_MAX_TOKENS / token-budget trimming dropped (agent-loop concern).
"""Interactive-element-tree assembly carried from Skyvern's scraper."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from playwright.async_api import Frame, Page

from .constants import SKYVERN_ID_ATTR, get_logger
from .page_utils import SkyvernFrame
from .scraped_page import ScrapeExcludeFunc

LOG = get_logger()


def calculate_sha256(data: str) -> str:
    """Helper function to calculate SHA256 hash of a string."""
    sha256_hash = hashlib.sha256()
    sha256_hash.update(data.encode())
    return sha256_hash.hexdigest()


RESERVED_ATTRIBUTES = {
    "accept",  # for input file
    "alt",
    "aria-checked",  # for option tag
    "aria-current",
    "aria-disabled",
    "aria-label",
    "aria-readonly",
    "aria-required",
    "aria-role",
    "aria-selected",  # for option tag
    "checked",
    "data-original-title",  # for bootstrap tooltip
    "data-ui",
    "disabled",  # for button
    "for",
    "href",  # For a tags
    "maxlength",
    "name",
    "pattern",
    "placeholder",
    "readonly",
    "required",
    "selected",  # for option tag
    "shape-description",  # for css shape
    "src",  # do we need this?
    "text-value",
    "title",
    "type",
    "value",
}


def _reserved_attributes_for_context() -> set[str]:
    # Trim: the enriched-tree context switch is dropped (skyvern_context removed);
    # the observation core uses the non-enriched default attribute set.
    return RESERVED_ATTRIBUTES


BASE64_INCLUDE_ATTRIBUTES = {
    "href",
    "src",
    "poster",
    "srcset",
    "icon",
}


def load_js_script() -> str:
    # Repointed to this package's carried domUtils.js (upstream loaded it from
    # SKYVERN_DIR/webeye/scraper/domUtils.js).
    path = str(Path(__file__).parent / "domUtils.js")
    try:
        # TODO: Implement TS of domUtils.js and use the complied JS file instead of the raw JS file.
        # This will allow our code to be type safe.
        with open(path) as f:
            return f.read()
    except FileNotFoundError as e:
        LOG.exception("Failed to load the JS script", path=path)
        raise e


JS_FUNCTION_DEFS = load_js_script()


def clean_element_before_hashing(element: dict) -> dict:
    def clean_nested(element: dict) -> dict:
        element_cleaned = {key: value for key, value in element.items() if key not in {"id", "rect", "frame_index"}}
        if "attributes" in element:
            attributes_cleaned = {key: value for key, value in element["attributes"].items() if key != SKYVERN_ID_ATTR}
            element_cleaned["attributes"] = attributes_cleaned
        if "children" in element:
            children_cleaned = [clean_nested(child) for child in element["children"]]
            element_cleaned["children"] = children_cleaned
        return element_cleaned

    return clean_nested(element)


def hash_element(element: dict) -> str:
    hash_ready_element = clean_element_before_hashing(element)
    # Sort the keys to ensure consistent ordering
    element_string = json.dumps(hash_ready_element, sort_keys=True)

    return calculate_sha256(element_string)


def build_element_dict(
    elements: list[dict],
) -> tuple[dict[str, str], dict[str, dict], dict[str, str], dict[str, str], dict[str, list[str]]]:
    id_to_css_dict: dict[str, str] = {}
    id_to_element_dict: dict[str, dict] = {}
    id_to_frame_dict: dict[str, str] = {}
    id_to_element_hash: dict[str, str] = {}
    hash_to_element_ids: dict[str, list[str]] = {}

    for element in elements:
        element_id: str = element.get("id", "")
        # get_interactable_element_tree marks each interactable element with a SKYVERN_ID_ATTR attribute
        id_to_css_dict[element_id] = f"[{SKYVERN_ID_ATTR}='{element_id}']"
        id_to_element_dict[element_id] = element
        id_to_frame_dict[element_id] = element["frame"]
        element_hash = hash_element(element)
        id_to_element_hash[element_id] = element_hash
        hash_to_element_ids[element_hash] = hash_to_element_ids.get(element_hash, []) + [element_id]

    return id_to_css_dict, id_to_element_dict, id_to_frame_dict, id_to_element_hash, hash_to_element_ids


async def get_frame_text(iframe: Frame, scrape_exclude: ScrapeExcludeFunc | None = None) -> str:
    """
    Get all the visible text in the iframe.
    :param iframe: Frame instance to get the text from.
    :param scrape_exclude: Optional ``filter_frames``-style predicate returning a
        ``ScrapeFrameDecision``; ``exclude`` means skip. The top-level caller must pass
        a starting frame the predicate would not itself exclude.
    :return: All the visible text from the iframe.
    """
    js_script = "() => document.body.innerText"

    try:
        text = await SkyvernFrame.evaluate(frame=iframe, expression=js_script)
        if text is None:
            text = ""
    except Exception:
        LOG.warning(
            "failed to get text from iframe",
            exc_info=True,
        )
        return ""

    for child_frame in iframe.child_frames:
        if child_frame.is_detached():
            continue

        # Skip excluded frames before any CDP probe.
        if scrape_exclude is not None and (await scrape_exclude(child_frame.page, child_frame)).exclude:
            continue

        try:
            child_frame_element = await child_frame.frame_element()
        except Exception:
            LOG.warning(
                "Unable to get child_frame_element",
                exc_info=True,
            )
            continue

        # it will get stuck when we `frame.evaluate()` on an invisible iframe
        if not await child_frame_element.is_visible():
            continue

        text += await get_frame_text(child_frame, scrape_exclude)

    return text


async def get_all_children_frames(page: Page) -> list[Frame]:
    start_index = 0
    frames = page.main_frame.child_frames

    while start_index < len(frames):
        frame = frames[start_index]
        start_index += 1
        frames.extend(frame.child_frames)

    return frames


async def filter_frames(
    frames: list[Frame], scrape_exclude: ScrapeExcludeFunc | None = None
) -> tuple[list[Frame], list[dict]]:
    """Split ``frames`` into the ones to scrape and any placeholder nodes to inject.

    Placeholder nodes come from ``scrape_exclude`` callbacks that skip a frame but want
    a non-interactable signal node left in its place (e.g. a cross-origin captcha the
    element tree cannot otherwise reach). Identical placeholders are de-duplicated so a
    vendor rendering the same widget across several frames only surfaces once.
    """
    filtered_frames: list[Frame] = []
    placeholder_nodes: list[dict] = []
    for frame in frames:
        if frame.is_detached():
            continue

        if scrape_exclude is None:
            filtered_frames.append(frame)
            continue

        decision = await scrape_exclude(frame.page, frame)
        if decision.placeholder is not None and decision.placeholder not in placeholder_nodes:
            placeholder_nodes.append(decision.placeholder)
        if decision.exclude:
            continue

        filtered_frames.append(frame)
    return filtered_frames, placeholder_nodes


async def add_frame_interactable_elements(
    frame: Frame,
    frame_index: int,
    elements: list[dict],
    element_tree: list[dict],
    must_included_tags: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Add the interactable element of the frame to the elements and element_tree.
    """
    try:
        frame_element = await frame.frame_element()
        # it will get stuck when we `frame.evaluate()` on an invisible iframe
        if not await frame_element.is_visible():
            return elements, element_tree
        skyvern_id = await frame_element.get_attribute(SKYVERN_ID_ATTR)
        if not skyvern_id:
            LOG.info(
                "No Skyvern id found for frame, skipping",
                frame_index=frame_index,
                attr=SKYVERN_ID_ATTR,
            )
            return elements, element_tree
    except Exception:
        LOG.warning(
            "Unable to get Skyvern id from frame_element",
            attr=SKYVERN_ID_ATTR,
            exc_info=True,
        )
        return elements, element_tree

    try:
        skyvern_frame = await SkyvernFrame.create_instance(frame)

        frame_elements, frame_element_tree = await skyvern_frame.build_tree_from_body(
            frame_name=skyvern_id, frame_index=frame_index, must_included_tags=must_included_tags
        )

        for element in elements:
            if element["id"] == skyvern_id:
                element["children"] = frame_element_tree

        elements = elements + frame_elements
    except Exception:
        LOG.warning("Failed to build the tree of the frame, skipping frame", frame_id=skyvern_id, exc_info=True)

    return elements, element_tree


async def get_interactable_element_tree(
    page: Page,
    scrape_exclude: ScrapeExcludeFunc | None = None,
    must_included_tags: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Get the element tree of the page, including all the elements that are interactable.
    :param page: Page instance to get the element tree from.
    :return: Tuple containing the element tree and a map of element IDs to elements.
    """
    # main page index is 0
    skyvern_page = await SkyvernFrame.create_instance(page)
    elements, element_tree = await skyvern_page.build_tree_from_body(
        frame_name="main.frame", frame_index=0, must_included_tags=must_included_tags
    )

    # Trim: upstream persisted frame indices on skyvern_context.frame_index_map so a
    # frame kept the same index across observations. The observation core assigns
    # indices per call; numbering within a single observation is identical.
    frame_index_map: dict[Frame, int] = {}
    all_frames = await get_all_children_frames(page)
    frames, placeholder_nodes = await filter_frames(all_frames, scrape_exclude)

    for frame in frames:
        frame_index = frame_index_map.get(frame, None)
        if frame_index is None:
            frame_index = len(frame_index_map) + 1
            frame_index_map[frame] = frame_index

    for frame in frames:
        frame_index = frame_index_map[frame]
        elements, element_tree = await add_frame_interactable_elements(
            frame,
            frame_index,
            elements,
            element_tree,
            must_included_tags,
        )

    # Placeholder nodes stand in for frames the filter skipped but wants the LLM to
    # still see (e.g. a cross-origin captcha inside a closed shadow root). They join the
    # element tree only — never ``elements`` — so they can never become a click target.
    element_tree.extend(placeholder_nodes)

    return elements, element_tree


def _should_keep_unique_id(element: dict) -> bool:
    # case where we shouldn't keep unique_id
    # 1. no readonly attr and not disable attr and no interactable
    # 2. readonly=false and disable=false and interactable=false

    if element.get("hoverOnly"):
        return True

    attributes = element.get("attributes", {})
    if (
        "disabled" not in attributes
        and "aria-disabled" not in attributes
        and "readonly" not in attributes
        and "aria-readonly" not in attributes
    ):
        return element.get("interactable", False)

    disabled = attributes.get("disabled")
    aria_disabled = attributes.get("aria-disabled")
    readonly = attributes.get("readonly")
    aria_readonly = attributes.get("aria-readonly")
    if disabled or aria_disabled or readonly or aria_readonly:
        return True
    return element.get("interactable", False)


def trim_element(element: dict) -> dict:
    queue = [element]
    while queue:
        queue_ele = queue.pop(0)
        if "frame" in queue_ele:
            del queue_ele["frame"]

        if "frame_index" in queue_ele:
            del queue_ele["frame_index"]

        if "id" in queue_ele and not _should_keep_unique_id(queue_ele):
            del queue_ele["id"]

        if "attributes" in queue_ele:
            new_attributes = _trimmed_base64_data(queue_ele["attributes"])
            if new_attributes:
                queue_ele["attributes"] = new_attributes
            else:
                del queue_ele["attributes"]

        if "attributes" in queue_ele and not queue_ele.get("keepAllAttr", False):
            has_pseudo = bool(queue_ele.get("beforePseudoText") or queue_ele.get("afterPseudoText"))
            is_icon_only = (
                queue_ele.get("interactable", False) and not str(queue_ele.get("text", "")).strip() and has_pseudo
            )
            new_attributes = _trimmed_attributes(queue_ele["attributes"], keep_class=is_icon_only)
            if new_attributes:
                queue_ele["attributes"] = new_attributes
            else:
                del queue_ele["attributes"]
        # remove the tag, don't need it in the HTML tree
        if "keepAllAttr" in queue_ele:
            del queue_ele["keepAllAttr"]

        if "children" in queue_ele:
            queue.extend(queue_ele["children"])
            if not queue_ele["children"]:
                del queue_ele["children"]
        if "text" in queue_ele:
            element_text = str(queue_ele["text"]).strip()
            if not element_text:
                del queue_ele["text"]

        if (
            "attributes" in queue_ele
            and "name" in queue_ele["attributes"]
            and len(queue_ele["attributes"]["name"]) > 500
        ):
            queue_ele["attributes"]["name"] = queue_ele["attributes"]["name"][:500]

        if "beforePseudoText" in queue_ele and not queue_ele.get("beforePseudoText"):
            del queue_ele["beforePseudoText"]

        if "afterPseudoText" in queue_ele and not queue_ele.get("afterPseudoText"):
            del queue_ele["afterPseudoText"]

    return element


def trim_element_tree(elements: list[dict]) -> list[dict]:
    for element in elements:
        trim_element(element)
    return elements


def _trimmed_base64_data(attributes: dict) -> dict:
    new_attributes: dict = {}

    for key in attributes:
        if key in BASE64_INCLUDE_ATTRIBUTES and "data:" in attributes.get(key, ""):
            continue
        new_attributes[key] = attributes[key]

    return new_attributes


def _trimmed_attributes(attributes: dict, *, keep_class: bool = False) -> dict:
    new_attributes: dict = {}
    reserved_attributes = _reserved_attributes_for_context()

    for key in attributes:
        if key == "role" and attributes[key] in ["listbox", "option"]:
            new_attributes[key] = attributes[key]
        if key in reserved_attributes:
            new_attributes[key] = attributes[key]

    if keep_class and "class" in attributes:
        cls = str(attributes["class"])
        if len(cls) > 100:
            last_space = cls.rfind(" ", 0, 100)
            cls = cls[: last_space if last_space > 0 else 100]
        new_attributes["class"] = cls

    return new_attributes


def _remove_unique_id(element: dict) -> None:
    if "attributes" not in element:
        return
    if SKYVERN_ID_ATTR in element["attributes"]:
        del element["attributes"][SKYVERN_ID_ATTR]


def _build_element_links(elements: list[dict]) -> None:
    """
    Build the links for listbox. A listbox could be mapped back to another element if:
        1. The listbox element's text matches context or text of an element
    """
    # first, build mapping between text/context and elements
    text_to_elements_map: dict[str, list[dict]] = defaultdict(list)
    context_to_elements_map: dict[str, list[dict]] = defaultdict(list)
    for element in elements:
        if "text" in element:
            text_to_elements_map[element["text"]].append(element)
        if "context" in element:
            context_to_elements_map[element["context"]].append(element)

    # then, build the links from element to listbox elements
    for element in elements:
        if not (
            "attributes" in element and "role" in element["attributes"] and "listbox" == element["attributes"]["role"]
        ):
            continue
        listbox_text = element["text"] if "text" in element else ""

        # WARNING: If a listbox has really little commont content (yes/no, etc.),
        #   it might have conflict and will connect to wrong element
        # if len(listbox_text) < 10:
        #     # do not support small listbox text for now as it's error proning. larger text match is more reliable
        #     LOG.info("Skip because too short listbox text", listbox_text=listbox_text)
        #     continue

        for text, linked_elements in text_to_elements_map.items():
            if listbox_text in text:
                for linked_element in linked_elements:
                    if linked_element["id"] != element["id"]:
                        LOG.info(
                            "Match listbox to target element text",
                            listbox_text=listbox_text,
                            text=text,
                            listbox_id=element["id"],
                            linked_element_id=linked_element["id"],
                        )
                        linked_element["linked_element"] = element["id"]

        for context, linked_elements in context_to_elements_map.items():
            if listbox_text in context:
                for linked_element in linked_elements:
                    if linked_element["id"] != element["id"]:
                        LOG.info(
                            "Match listbox to target element context",
                            listbox_text=listbox_text,
                            context=context,
                            listbox_id=element["id"],
                            linked_element_id=linked_element["id"],
                        )
                        linked_element["linked_element"] = element["id"]
