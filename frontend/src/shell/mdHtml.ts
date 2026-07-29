// The markdown ⇄ DOM pair behind the resume views (dependency-free, on purpose).
//
// `mdToHtml` renders our small markdown dialect to an HTML string — the SAME
// output whether it feeds the read-only <Markdown> view or the editable Preview
// surface (MarkdownEditor), so the two can never drift apart.
//
// `domToMarkdown` is the inverse: it serializes the contentEditable DOM (which
// the browser mutates freely — <div> paragraphs, <b>/<i> from execCommand,
// stray <span>s, <br>s) back into that dialect. Markdown stays the document of
// record: every keystroke in the editable Preview round-trips DOM → markdown,
// so Raw mode, save, tailoring, and PDF export always see clean markdown and
// nothing ever reformats a resume behind the user's back.
//
// Dialect (kept deliberately small — this is what the pair guarantees):
//   #/##/### headings · **bold** · *italic* · `code` · [label](url) ·
//   - bullets · 1. numbered · > quotes · | tables | · --- rules · paragraphs.
//
// Fidelity contract: for canonical input (blank line between blocks),
// domToMarkdown(mdToHtml(md)) === md — pinned by mdHtml.test.ts. Non-canonical
// input normalizes once (extra blank lines collapse) and is stable after.

const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*\s][^*]*\*)/g;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(text: string): string {
  return escapeHtml(text).replace(/"/g, "&quot;");
}

/** http(s)/mailto/tel only — anything else renders as plain text, so a
 *  javascript: URL in scraped/LLM content can never become a live link. */
function safeHref(href: string): string | null {
  return /^(https?:|mailto:|tel:)/i.test(href.trim()) ? href.trim() : null;
}

function inlineHtml(text: string): string {
  return text
    .split(INLINE_RE)
    .map((part) => {
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        return `<strong>${escapeHtml(part.slice(2, -2))}</strong>`;
      }
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        return `<code class="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11.5px]">${escapeHtml(part.slice(1, -1))}</code>`;
      }
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
      if (link) {
        const href = safeHref(link[2]);
        if (href) {
          return `<a href="${escapeAttr(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(link[1])}</a>`;
        }
        return escapeHtml(part);
      }
      if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
        // <i>, not <em> — .resume-prose repurposes em as mono annotation text.
        return `<i>${escapeHtml(part.slice(1, -1))}</i>`;
      }
      return escapeHtml(part);
    })
    .join("");
}

function tableHtml(rows: string[]): string {
  const cells = rows
    .filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)) // drop the separator row
    .map((r) =>
      r
        .split("|")
        .map((c) => c.trim())
        .filter((c, i, a) => !(c === "" && (i === 0 || i === a.length - 1))),
    );
  const [head, ...body] = cells;
  const th = (head ?? [])
    .map(
      (c) =>
        `<th class="border-b border-border px-2 py-1 text-left font-medium text-ink-3">${inlineHtml(c)}</th>`,
    )
    .join("");
  const trs = body
    .map(
      (r) =>
        `<tr>${r.map((c) => `<td class="border-b border-border/60 px-2 py-1 text-ink-2">${inlineHtml(c)}</td>`).join("")}</tr>`,
    )
    .join("");
  return `<div class="my-2 overflow-x-auto"><table class="w-full border-collapse text-[12px]"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`;
}

/** Render the dialect to an HTML string (block elements, no outer wrapper). */
export function mdToHtml(md: string): string {
  const out: string[] = [];
  let ul: string[] = [];
  let ol: string[] = [];
  let quote: string[] = [];
  let table: string[] = [];

  const flushUl = () => {
    if (ul.length) out.push(`<ul>${ul.map((li) => `<li>${inlineHtml(li)}</li>`).join("")}</ul>`);
    ul = [];
  };
  const flushOl = () => {
    if (ol.length) out.push(`<ol>${ol.map((li) => `<li>${inlineHtml(li)}</li>`).join("")}</ol>`);
    ol = [];
  };
  const flushQuote = () => {
    if (quote.length)
      out.push(`<blockquote>${quote.map((q) => `<p>${inlineHtml(q)}</p>`).join("")}</blockquote>`);
    quote = [];
  };
  const flushTable = () => {
    if (table.length) out.push(tableHtml(table));
    table = [];
  };
  const flushAll = () => {
    flushUl();
    flushOl();
    flushQuote();
    flushTable();
  };

  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("|")) {
      flushUl();
      flushOl();
      flushQuote();
      table.push(line);
      continue;
    }
    flushTable();
    if (line.startsWith("- ")) {
      flushOl();
      flushQuote();
      ul.push(line.slice(2));
      continue;
    }
    const numbered = /^\d+\.\s+(.*)$/.exec(line);
    if (numbered) {
      flushUl();
      flushQuote();
      ol.push(numbered[1]);
      continue;
    }
    if (line.startsWith("> ")) {
      flushUl();
      flushOl();
      quote.push(line.slice(2));
      continue;
    }
    flushAll();
    if (line.startsWith("### ")) out.push(`<h3>${inlineHtml(line.slice(4))}</h3>`);
    else if (line.startsWith("## ")) out.push(`<h2>${inlineHtml(line.slice(3))}</h2>`);
    else if (line.startsWith("# ")) out.push(`<h1>${inlineHtml(line.slice(2))}</h1>`);
    else if (line === "---") out.push("<hr>");
    else if (line.trim() !== "") out.push(`<p>${inlineHtml(line)}</p>`);
  }
  flushAll();
  return out.join("");
}

// ---------------------------------------------------------------------------
// DOM → markdown
// ---------------------------------------------------------------------------

/** Serialize a node's inline CONTENT (its children). Text comes back verbatim
 *  (markdown the user typed literally survives as markdown — this IS a
 *  markdown editor); formatting elements map back to their dialect tokens. */
function inlineMd(node: Node): string {
  let out = "";
  for (const child of Array.from(node.childNodes)) out += inlineNodeMd(child);
  return out;
}

/** Serialize ONE inline node, wrapper included — shared by inlineMd's loop and
 *  the root-level inline-run pass (a bare <b> at the root must keep its **). */
function inlineNodeMd(child: Node): string {
  if (child.nodeType === Node.TEXT_NODE) {
    return (child.textContent ?? "").replace(/\u00a0/g, " ");
  }
  if (child.nodeType !== Node.ELEMENT_NODE) return "";
  const el = child as HTMLElement;
  const tag = el.tagName;
  if (tag === "UL" || tag === "OL") return ""; // nested lists: the block pass owns them
  if (tag === "BR") return "\n";
  const inner = inlineMd(el);
  if (tag === "STRONG" || tag === "B") return inner ? `**${inner}**` : "";
  if (tag === "I" || tag === "EM") return inner ? `*${inner}*` : "";
  if (tag === "CODE") return inner ? `\`${inner}\`` : "";
  if (tag === "A") {
    const href = el.getAttribute("href") ?? "";
    return inner ? `[${inner}](${href})` : "";
  }
  return inner; // SPAN / FONT / anything else: keep the text, drop the wrapper
}

function listItems(listEl: HTMLElement): string[] {
  const items: string[] = [];
  for (const li of Array.from(listEl.children)) {
    if (li.tagName !== "LI") continue;
    const line = inlineMd(li).replace(/\n+/g, " ").trim();
    if (line) items.push(line);
    // Flatten a browser-nested list one level (our dialect has no nesting).
    for (const nested of Array.from(li.children)) {
      if (nested.tagName === "UL" || nested.tagName === "OL") {
        items.push(...listItems(nested as HTMLElement));
      }
    }
  }
  return items;
}

function tableMd(tableEl: HTMLElement): string {
  const rows: string[][] = [];
  for (const tr of Array.from(tableEl.querySelectorAll("tr"))) {
    const cells = Array.from(tr.children)
      .filter((c) => c.tagName === "TH" || c.tagName === "TD")
      .map((c) => inlineMd(c).replace(/\n+/g, " ").trim());
    if (cells.length) rows.push(cells);
  }
  if (!rows.length) return "";
  const lines = [`| ${rows[0].join(" | ")} |`, `| ${rows[0].map(() => "---").join(" | ")} |`];
  for (const r of rows.slice(1)) lines.push(`| ${r.join(" | ")} |`);
  return lines.join("\n");
}

const BLOCK_TAGS = new Set([
  "H1", "H2", "H3", "H4", "H5", "H6",
  "UL", "OL", "BLOCKQUOTE", "HR", "TABLE", "DIV", "P", "SECTION", "ARTICLE",
]);

/** Serialize the editable surface back to the dialect. Tolerates everything
 *  contentEditable produces: <div> paragraphs, <b>/<i>, <br>-only spacers,
 *  root-level text nodes, nested lists. Consecutive inline nodes at the root
 *  (text, spans, formatting runs) group into ONE paragraph — they are one
 *  visual line; splitting them would corrupt the text. Blocks join with one
 *  blank line. */
export function domToMarkdown(root: HTMLElement): string {
  const blocks: string[] = [];
  const push = (s: string) => {
    if (s.trim() !== "") blocks.push(s.replace(/\n{3,}/g, "\n\n"));
  };

  // Accumulator for a run of root-level inline content (one visual line).
  let run = "";
  const flushRun = () => {
    push(run);
    run = "";
  };

  for (const child of Array.from(root.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      run += (child.textContent ?? "").replace(/\u00a0/g, " ");
      continue;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue;
    const el = child as HTMLElement;
    if (!BLOCK_TAGS.has(el.tagName)) {
      // Inline at the root (BR, SPAN, B, I, CODE, A, …) — part of the run.
      run += inlineNodeMd(el);
      continue;
    }
    flushRun();
    switch (el.tagName) {
      case "H1":
        push(`# ${inlineMd(el).trim()}`);
        break;
      case "H2":
        push(`## ${inlineMd(el).trim()}`);
        break;
      case "H3":
      case "H4":
      case "H5":
      case "H6":
        push(`### ${inlineMd(el).trim()}`);
        break;
      case "UL":
        push(listItems(el).map((li) => `- ${li}`).join("\n"));
        break;
      case "OL":
        push(listItems(el).map((li, i) => `${i + 1}. ${li}`).join("\n"));
        break;
      case "BLOCKQUOTE":
        push(
          domToMarkdown(el)
            .split("\n")
            .filter((l) => l.trim() !== "")
            .map((l) => `> ${l}`)
            .join("\n"),
        );
        break;
      case "HR":
        push("---");
        break;
      case "TABLE":
        push(tableMd(el));
        break;
      case "DIV":
      case "P":
      case "SECTION":
      case "ARTICLE": {
        // A div may itself wrap real blocks (paste artifacts); recurse when it
        // contains block children, else treat it as one paragraph.
        const hasBlockChild = Array.from(el.children).some((c) =>
          ["H1", "H2", "H3", "H4", "UL", "OL", "TABLE", "HR", "BLOCKQUOTE", "DIV", "P"].includes(
            c.tagName,
          ),
        );
        if (hasBlockChild) {
          const inner = domToMarkdown(el);
          if (inner.trim() !== "") blocks.push(inner);
        } else {
          push(inlineMd(el));
        }
        break;
      }
      default:
        push(inlineMd(el)); // unreachable (non-block tags joined the run above)
    }
  }
  flushRun(); // trailing inline run — without this, text after the last block is lost
  // Blocks are separated by exactly one blank line; a paragraph that contains
  // hard newlines (from <br>) splits into its own lines first.
  return blocks
    .flatMap((b) => b.split(/\n{2,}/))
    .map((b) => b.replace(/[ \t]+$/gm, ""))
    .filter((b) => b.trim() !== "")
    .join("\n\n");
}
