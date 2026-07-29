// The resume editor — dependency-free WYSIWYG-lite over our markdown dialect.
//
// Preview mode is ONE directly-editable rendered surface (contentEditable):
// the user clicks into the formatted text and types; the toolbar applies real
// formatting to it. Every edit serializes the DOM back to markdown
// (shell/mdHtml.ts), so markdown stays the document of record — Raw mode, save,
// tailoring, and PDF export all see clean markdown and nothing reformats the
// user's text behind their back. Raw mode is the same single pane as plain
// markdown source, for power users.
//
// React + contentEditable ground rule: while the user types, the DOM is the
// source of truth — we serialize OUT on every input but never render back IN
// (that would destroy the caret). The surface re-renders from markdown only on
// an external value change (Generate finishing, a PDF upload, a mode switch)
// and on blur (normalization; the caret is gone then anyway).

import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { Markdown } from "./Markdown";
import { domToMarkdown, mdToHtml } from "./mdHtml";

export type EditorMode = "preview" | "raw";

function caretToEnd(el: HTMLElement) {
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

/** execCommand is deprecated-but-universal in the WebViews we ship into
 *  (WKWebView / WebView2 / WebKitGTK) and in dev Chrome; formatBlock's argument
 *  quirk ("h2" vs "<h2>") differs per engine, so try both. */
function formatBlock(tag: string) {
  if (!document.execCommand("formatBlock", false, tag)) {
    document.execCommand("formatBlock", false, `<${tag}>`);
  }
}

/** Wrap the current selection in `tag` via a real Range — deterministic where
 *  execCommand('bold') is not (engines skip it in some states). Returns false
 *  for a collapsed or element-crossing selection (caller falls back to literal
 *  markdown tokens, which normalize into the same formatting on blur). */
export function wrapSelectionInTag(surface: HTMLElement, tag: string): boolean {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return false;
  const range = sel.getRangeAt(0);
  if (!surface.contains(range.commonAncestorContainer)) return false;
  try {
    const wrapper = document.createElement(tag);
    range.surroundContents(wrapper); // throws if the selection crosses elements
    sel.removeAllRanges();
    const after = document.createRange();
    after.selectNodeContents(wrapper);
    sel.addRange(after);
    return true;
  } catch {
    return false;
  }
}

// The tags that count as "already this format" when deciding whether a toolbar
// click should toggle OFF — bold serializes from STRONG *or* B, italic from
// I *or* EM (both mdToHtml and execCommand can produce either).
const FORMAT_SYNONYMS: Record<string, readonly string[]> = {
  strong: ["STRONG", "B"],
  i: ["I", "EM"],
  code: ["CODE"],
};

/** The nearest ancestor of the selection (bounded by `surface`) that already
 *  applies the same inline format as `tag` — e.g. a STRONG/B wrapping the
 *  caret for bold. Null when the selection is not inside one, so the tool
 *  should wrap rather than unwrap. This is what makes B / I / `</>` TOGGLE
 *  instead of stacking another layer of markers on already-formatted text. */
export function enclosingInlineFormat(surface: HTMLElement, tag: string): HTMLElement | null {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  if (!surface.contains(range.commonAncestorContainer)) return null;
  const wanted = FORMAT_SYNONYMS[tag] ?? [tag.toUpperCase()];
  let node: Node | null = range.commonAncestorContainer;
  while (node && node !== surface) {
    if (node.nodeType === Node.ELEMENT_NODE && wanted.includes((node as HTMLElement).tagName)) {
      return node as HTMLElement;
    }
    node = node.parentNode;
  }
  return null;
}

/** Toggle OFF: lift `el`'s children into its parent and drop the wrapper,
 *  keeping the freed content selected so an immediate re-click re-wraps the
 *  same text. */
export function unwrapInlineFormat(el: HTMLElement): void {
  const parent = el.parentNode;
  if (!parent) return;
  const first = el.firstChild;
  const last = el.lastChild;
  while (el.firstChild) parent.insertBefore(el.firstChild, el);
  parent.removeChild(el);
  if (!first) return; // the wrapper had no contents
  const sel = window.getSelection();
  const range = document.createRange();
  range.setStartBefore(first);
  range.setEndAfter(last ?? first);
  sel?.removeAllRanges();
  sel?.addRange(range);
}

/** Toggle inline `tag` over the selection: unwrap when it already sits inside
 *  such an element, else wrap it. "none" means neither applied (a collapsed or
 *  element-crossing selection) and the caller falls back to literal tokens. */
export function toggleInlineFormat(
  surface: HTMLElement,
  tag: string,
): "wrapped" | "unwrapped" | "none" {
  const existing = enclosingInlineFormat(surface, tag);
  if (existing) {
    unwrapInlineFormat(existing);
    return "unwrapped";
  }
  return wrapSelectionInTag(surface, tag) ? "wrapped" : "none";
}

type ToolAction =
  | { kind: "exec"; command: string }
  | { kind: "block"; tag: string }
  | { kind: "wrapTag"; tag: string; before: string; after: string }
  | { kind: "wrap"; before: string; after: string };

const TOOLS: { key: string; label: string; title: string; action: ToolAction }[] = [
  { key: "bold", label: "B", title: "shell.mdEditor.bold", action: { kind: "wrapTag", tag: "strong", before: "**", after: "**" } },
  { key: "italic", label: "I", title: "shell.mdEditor.italic", action: { kind: "wrapTag", tag: "i", before: "*", after: "*" } },
  { key: "h1", label: "H1", title: "shell.mdEditor.h1", action: { kind: "block", tag: "h1" } },
  { key: "h2", label: "H2", title: "shell.mdEditor.h2", action: { kind: "block", tag: "h2" } },
  { key: "h3", label: "H3", title: "shell.mdEditor.h3", action: { kind: "block", tag: "h3" } },
  { key: "para", label: "¶", title: "shell.mdEditor.paragraph", action: { kind: "block", tag: "p" } },
  { key: "bullet", label: "•", title: "shell.mdEditor.bullet", action: { kind: "exec", command: "insertUnorderedList" } },
  { key: "numbered", label: "1.", title: "shell.mdEditor.numbered", action: { kind: "exec", command: "insertOrderedList" } },
  { key: "quote", label: "❝", title: "shell.mdEditor.quote", action: { kind: "block", tag: "blockquote" } },
  { key: "code", label: "</>", title: "shell.mdEditor.code", action: { kind: "wrapTag", tag: "code", before: "`", after: "`" } },
  { key: "link", label: "🔗", title: "shell.mdEditor.link", action: { kind: "wrap", before: "[", after: "](url)" } },
];

export function MarkdownEditor({
  value,
  onChange,
  mode,
  readOnly = false,
  testid,
}: {
  value: string;
  onChange?: (v: string) => void;
  mode: EditorMode;
  readOnly?: boolean;
  testid?: string;
}) {
  const { t } = useTranslation();
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  // The last markdown WE emitted — when the incoming prop equals it, the DOM is
  // already ahead of React state and must not be re-rendered (caret safety).
  const lastEmitted = useRef<string | null>(null);

  // Render markdown → DOM only on mount and genuine external changes.
  useEffect(() => {
    // Leaving preview unmounts the surface — forget the emitted marker so the
    // NEXT preview mount always re-renders. Without this, Raw → Preview with no
    // Raw edits skipped the render and showed a blank surface (value equalled
    // lastEmitted, but the DOM it described was gone).
    if (mode !== "preview") {
      lastEmitted.current = null;
      return;
    }
    const el = surfaceRef.current;
    if (!el || readOnly) return;
    if (value !== lastEmitted.current) {
      el.innerHTML = mdToHtml(value);
      lastEmitted.current = value;
    }
  }, [value, mode, readOnly]);

  if (readOnly) {
    return (
      <div data-testid={testid} className="h-full overflow-auto rounded-md border border-border bg-surface p-4">
        <Markdown md={value} />
      </div>
    );
  }

  if (mode === "raw") {
    return (
      <textarea
        data-testid={testid}
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        className="h-full w-full resize-none rounded-md border border-accent bg-surface p-3 font-mono text-[12px] leading-relaxed text-ink focus:border-accent focus:outline-none"
      />
    );
  }

  const emit = () => {
    const el = surfaceRef.current;
    if (!el) return;
    const md = domToMarkdown(el);
    lastEmitted.current = md;
    onChange?.(md);
  };

  const runTool = (action: ToolAction) => {
    const el = surfaceRef.current;
    if (!el) return;
    el.focus();
    if (action.kind === "exec") {
      document.execCommand(action.command);
    } else if (action.kind === "block") {
      formatBlock(action.tag);
    } else if (action.kind === "wrapTag" && toggleInlineFormat(el, action.tag) !== "none") {
      // Toggled in place (wrapped, or unwrapped if it was already formatted) —
      // selection kept, nothing else to do.
    } else {
      // Fallback: a collapsed / element-crossing wrapTag selection, or the link
      // tool — insert the literal markdown tokens; they render into real
      // formatting on the next normalization (blur / mode switch).
      const sel = window.getSelection()?.toString() ?? "";
      document.execCommand("insertText", false, `${action.before}${sel}${action.after}`);
    }
    emit();
  };

  const onPaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
    const text = e.clipboardData.getData("text/plain");
    if (!text) return; // let the browser try whatever else is on the clipboard
    e.preventDefault();
    const el = surfaceRef.current;
    if (!el) return;
    if (el.innerText.trim() === "") {
      // Pasting into an empty editor — the "paste your own from ChatGPT/Gemini"
      // path. The pasted text IS markdown: adopt it wholesale and render it.
      lastEmitted.current = text;
      onChange?.(text);
      el.innerHTML = mdToHtml(text);
      caretToEnd(el);
      return;
    }
    // Mid-document paste: insert as plain text at the caret (keeps undo), then
    // serialize. Any markdown syntax in it renders on the next normalization.
    document.execCommand("insertText", false, text);
    emit();
  };

  const onBlur = () => {
    // Normalize: re-render the canonical form (typed markdown syntax like
    // **bold** becomes bold). The caret is gone on blur, so this is free.
    const el = surfaceRef.current;
    if (el && lastEmitted.current !== null) {
      el.innerHTML = mdToHtml(lastEmitted.current);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        className="mb-2 flex flex-wrap items-center gap-1 rounded-md border border-border bg-surface-2 px-1.5 py-1"
        data-testid="md-toolbar"
      >
        {TOOLS.map((tool) => (
          <button
            key={tool.key}
            type="button"
            title={t(tool.title)}
            aria-label={t(tool.title)}
            data-testid={`md-tool-${tool.key}`}
            // preventDefault keeps focus + selection in the editable surface.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => runTool(tool.action)}
            className="min-w-[26px] rounded px-1.5 py-0.5 text-center text-[12px] font-medium text-ink-2 hover:bg-surface-3"
          >
            {tool.label}
          </button>
        ))}
      </div>
      <div
        ref={surfaceRef}
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline="true"
        spellCheck={false}
        data-testid={testid}
        data-placeholder={t("shell.mdEditor.placeholder")}
        onInput={emit}
        onPaste={onPaste}
        onBlur={onBlur}
        className="resume-prose md-editable min-h-0 flex-1 cursor-text overflow-auto rounded-md border border-border bg-surface p-4 focus:border-accent focus:outline-none"
      />
    </div>
  );
}
