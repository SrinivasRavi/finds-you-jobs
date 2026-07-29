// Read-only markdown view — a thin wrapper over the shared mdToHtml renderer
// (shell/mdHtml.ts), the SAME renderer the editable Preview surface uses, so
// the read-only and editable views can never drift apart. The dialect is small
// on purpose (headings, bold, italic, code, links, lists, quotes, tables,
// rules, paragraphs); mdToHtml escapes all text and refuses non-http(s)/mailto
// link schemes, so scraped/LLM content can't inject markup.

import { mdToHtml } from "./mdHtml";

export function Markdown({ md, className = "" }: { md: string; className?: string }) {
  return (
    <div
      className={`resume-prose ${className}`}
      // Safe: mdToHtml escapes text/attributes and allowlists link schemes.
      dangerouslySetInnerHTML={{ __html: mdToHtml(md) }}
    />
  );
}
