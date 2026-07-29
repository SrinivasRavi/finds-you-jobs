// The resume-fidelity contract for the editable Preview surface: markdown
// renders to DOM, the user edits the DOM, and serialization must give back the
// same markdown — for canonical input, byte-for-byte. If this suite fails, the
// editor is mangling resumes; do not weaken these assertions to make it pass.

import { describe, expect, it } from "vitest";

import { domToMarkdown, mdToHtml } from "./mdHtml";

function roundTrip(md: string): string {
  const host = document.createElement("div");
  host.innerHTML = mdToHtml(md);
  return domToMarkdown(host);
}

const CANONICAL_RESUME = `# Tenet Loader

**Headline:** Forward-deployed engineer building distributed backends.

**Email:** [tenetloader@gmail.com](mailto:tenetloader@gmail.com)

## Experience

- Owned the billing platform: event-driven pipelines in Python and Go.
- Postgres at scale, \`Kubernetes\` across three regions.

## Highlights

1. Cut p99 latency by 40%.
2. Led a team of five engineers.

---

> Available to start immediately.

| Skill | Years |
| --- | --- |
| Python | 7 |
| Go | 3 |`;

describe("mdToHtml", () => {
  it("renders every dialect construct", () => {
    const html = mdToHtml(CANONICAL_RESUME);
    expect(html).toContain("<h1>Tenet Loader</h1>");
    expect(html).toContain("<h2>Experience</h2>");
    expect(html).toContain("<strong>Headline:</strong>");
    expect(html).toContain("<code");
    expect(html).toContain('href="mailto:tenetloader@gmail.com"');
    expect(html).toContain("<ul><li>");
    expect(html).toContain("<ol><li>");
    expect(html).toContain("<hr>");
    expect(html).toContain("<blockquote><p>Available to start immediately.</p></blockquote>");
    expect(html).toContain("<table");
    // italic is not in the sample — check the construct directly:
    expect(mdToHtml("*soft skill*")).toContain("<i>soft skill</i>");
  });

  it("escapes HTML in text — content can never inject markup", () => {
    const html = mdToHtml("Hello <script>alert(1)</script> & <b>bye</b>");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&amp;");
    expect(html).not.toContain("<b>");
  });

  it("refuses non-http(s)/mailto link schemes", () => {
    const html = mdToHtml("[click](javascript:alert(1))");
    expect(html).not.toContain("<a");
    expect(html).toContain("[click](javascript:alert(1))");
    expect(mdToHtml("[ok](https://example.com)")).toContain('href="https://example.com"');
  });
});

describe("round-trip fidelity", () => {
  it("canonical resume survives byte-for-byte", () => {
    expect(roundTrip(CANONICAL_RESUME)).toBe(CANONICAL_RESUME);
  });

  it("each construct survives alone", () => {
    for (const md of [
      "# H1",
      "## H2",
      "### H3",
      "plain paragraph",
      "**bold** middle *italic* and `code`",
      "[label](https://example.com/x)",
      "- one\n- two",
      "1. first\n2. second",
      "> quoted line",
      "---",
      "| A | B |\n| --- | --- |\n| 1 | 2 |",
    ]) {
      expect(roundTrip(md)).toBe(md);
    }
  });

  it("is idempotent: a second pass changes nothing", () => {
    const once = roundTrip(CANONICAL_RESUME);
    expect(roundTrip(once)).toBe(once);
  });

  it("non-canonical spacing normalizes once, then is stable", () => {
    const messy = "# Title\n\n\n\nText\n- a\n- b";
    const once = roundTrip(messy);
    expect(once).toBe("# Title\n\nText\n\n- a\n- b");
    expect(roundTrip(once)).toBe(once);
  });
});

describe("domToMarkdown tolerates browser-generated DOM", () => {
  function serialize(html: string): string {
    const host = document.createElement("div");
    host.innerHTML = html;
    return domToMarkdown(host);
  }

  it("divs as paragraphs, <b>/<i> from execCommand, br-only spacers", () => {
    expect(
      serialize("<div>first</div><div><br></div><div><b>bold</b> and <i>soft</i></div>"),
    ).toBe("first\n\n**bold** and *soft*");
  });

  it("consecutive root inline nodes group into ONE paragraph", () => {
    // One visual line must serialize as one paragraph — splitting a text node
    // from its neighboring <span>/<b> would corrupt the sentence.
    expect(serialize("loose text<span> more</span>")).toBe("loose text more");
    expect(serialize("a <b>bold</b> tail")).toBe("a **bold** tail");
  });

  it("keeps typed markdown syntax verbatim (it IS a markdown editor)", () => {
    expect(serialize("<div># typed heading</div>")).toBe("# typed heading");
  });

  it("pasted-markdown text round-trips through adopt-and-render", () => {
    // The empty-editor paste path: raw pasted markdown is adopted as the value
    // and rendered; serializing that render returns the same document.
    const pasted = "# My own resume\n\nPasted from my ChatGPT.\n\n- skill one\n- skill two";
    expect(roundTrip(pasted)).toBe(pasted);
  });

  it("flattens a browser-nested list one level", () => {
    expect(serialize("<ul><li>a<ul><li>a1</li></ul></li><li>b</li></ul>")).toBe(
      "- a\n- a1\n- b",
    );
  });

  it("nbsp from contentEditable becomes a plain space", () => {
    expect(serialize("<div>a b</div>")).toBe("a b");
  });
});
