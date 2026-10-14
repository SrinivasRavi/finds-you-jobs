// Read-only markdown view over mdToHtml, the same renderer as the editable
// Preview, so the 2 can't drift. mdToHtml escapes all text and refuses
// non-http(s)/mailto links, so scraped or LLM content can't inject markup.

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
