// The toolbar toggle contract for the editable Preview surface: clicking B / I
// / `</>` on already-formatted text must REMOVE the formatting, never stack
// another layer of markers (**Postgres** → click Bold again → Postgres, not
// *****Postgres*****). Exercised at the DOM level (jsdom) because the logic is
// pure Range/selection manipulation; the md↔DOM fidelity it feeds is pinned by
// mdHtml.test.ts, and the live click path by e2e/resume-editor.spec.ts.

import { afterEach, describe, expect, it } from "vitest";

import { domToMarkdown } from "./mdHtml";
import {
  enclosingInlineFormat,
  toggleInlineFormat,
  unwrapInlineFormat,
  wrapSelectionInTag,
} from "./MarkdownEditor";

let host: HTMLElement | null = null;

function mount(html: string): HTMLElement {
  host = document.createElement("div");
  host.innerHTML = html;
  document.body.appendChild(host);
  return host;
}

afterEach(() => {
  host?.remove();
  host = null;
  window.getSelection()?.removeAllRanges();
});

/** Select the substring `text` wherever it first appears in `root`. */
function selectText(root: HTMLElement, text: string): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    const i = node.textContent?.indexOf(text) ?? -1;
    if (i >= 0) {
      const range = document.createRange();
      range.setStart(node, i);
      range.setEnd(node, i + text.length);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      return;
    }
  }
  throw new Error(`text not found: ${text}`);
}

/** Collapse the caret to inside the first element matching `selector`. */
function caretInside(root: HTMLElement, selector: string): void {
  const el = root.querySelector(selector);
  if (!el?.firstChild) throw new Error(`no ${selector}`);
  const range = document.createRange();
  range.setStart(el.firstChild, 1);
  range.collapse(true);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

describe("inline-format toggle", () => {
  it("bold: first click wraps a plain selection", () => {
    const el = mount("<p>Postgres at scale</p>");
    selectText(el, "Postgres");
    expect(toggleInlineFormat(el, "strong")).toBe("wrapped");
    expect(el.querySelectorAll("strong")).toHaveLength(1);
    expect(domToMarkdown(el)).toBe("**Postgres** at scale");
  });

  it("bold: a second click UNWRAPS instead of stacking ** (the reported bug)", () => {
    const el = mount("<p>Postgres at scale</p>");
    selectText(el, "Postgres");
    toggleInlineFormat(el, "strong"); // now <strong>Postgres</strong>, selection kept
    expect(toggleInlineFormat(el, "strong")).toBe("unwrapped");
    expect(el.querySelector("strong")).toBeNull();
    // No ***** stacking — clean plain markdown, byte-for-byte.
    expect(domToMarkdown(el)).toBe("Postgres at scale");
  });

  it("bold: toggling twice more is stable (wrap → unwrap → wrap)", () => {
    const el = mount("<p>Postgres at scale</p>");
    selectText(el, "Postgres");
    expect(toggleInlineFormat(el, "strong")).toBe("wrapped");
    expect(toggleInlineFormat(el, "strong")).toBe("unwrapped");
    expect(toggleInlineFormat(el, "strong")).toBe("wrapped");
    expect(domToMarkdown(el)).toBe("**Postgres** at scale");
  });

  it("bold: a collapsed caret inside existing <strong> toggles it off", () => {
    const el = mount("<p><strong>Postgres</strong> at scale</p>");
    caretInside(el, "strong");
    expect(toggleInlineFormat(el, "strong")).toBe("unwrapped");
    expect(domToMarkdown(el)).toBe("Postgres at scale");
  });

  it("bold: recognizes a browser-produced <b> as already-bold (execCommand quirk)", () => {
    const el = mount("<p><b>Postgres</b> at scale</p>");
    selectText(el, "Postgres");
    expect(enclosingInlineFormat(el, "strong")?.tagName).toBe("B");
    expect(toggleInlineFormat(el, "strong")).toBe("unwrapped");
    expect(domToMarkdown(el)).toBe("Postgres at scale");
  });

  it("italic: matches both <i> and <em>, toggling either off", () => {
    for (const html of ["<p><i>soft</i> skill</p>", "<p><em>soft</em> skill</p>"]) {
      const el = mount(html);
      selectText(el, "soft");
      expect(toggleInlineFormat(el, "i")).toBe("unwrapped");
      expect(domToMarkdown(el)).toBe("soft skill");
      el.remove();
    }
  });

  it("code: toggles a `code` span off", () => {
    const el = mount("<p><code>npm</code> install</p>");
    selectText(el, "npm");
    expect(toggleInlineFormat(el, "code")).toBe("unwrapped");
    expect(domToMarkdown(el)).toBe("npm install");
  });

  it("wrapping is unaffected by an unrelated existing format", () => {
    // Caret in a bold word, click Italic — must WRAP italic, not treat the
    // bold ancestor as an italic to toggle off.
    const el = mount("<p><strong>Postgres</strong> at scale</p>");
    selectText(el, "scale");
    expect(toggleInlineFormat(el, "i")).toBe("wrapped");
    expect(domToMarkdown(el)).toBe("**Postgres** at *scale*");
  });

  it("returns 'none' for a collapsed selection with no format to remove", () => {
    const el = mount("<p>plain text</p>");
    caretInside(el, "p");
    expect(toggleInlineFormat(el, "strong")).toBe("none");
  });

  it("unwrapInlineFormat leaves the freed text selected for immediate re-wrap", () => {
    const el = mount("<p><strong>Postgres</strong> at scale</p>");
    const strong = el.querySelector("strong")!;
    unwrapInlineFormat(strong);
    // Selection now spans the former contents — a re-wrap restores the bold.
    expect(wrapSelectionInTag(el, "strong")).toBe(true);
    expect(domToMarkdown(el)).toBe("**Postgres** at scale");
  });
});
