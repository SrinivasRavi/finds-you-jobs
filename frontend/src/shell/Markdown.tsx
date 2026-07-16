// Minimal markdown renderer — enough for JD descriptions, resume/cover prose,
// and the scorer breakdown table. Intentionally tiny (no dependency); handles
// #/##/### headings, **bold**, `code`, - bullets, | tables |, --- rules, and
// paragraphs. Content is our own mock data, not untrusted input.

import { type ReactNode } from "react";

function inline(text: string, key: string): ReactNode {
  // Split on **bold** and `code`, keep delimiters.
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => {
    if (p.startsWith("**") && p.endsWith("**")) {
      return <strong key={`${key}-${i}`}>{p.slice(2, -2)}</strong>;
    }
    if (p.startsWith("`") && p.endsWith("`")) {
      return (
        <code key={`${key}-${i}`} className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11.5px]">
          {p.slice(1, -1)}
        </code>
      );
    }
    return <span key={`${key}-${i}`}>{p}</span>;
  });
}

export function Markdown({ md, className = "" }: { md: string; className?: string }) {
  const lines = md.split("\n");
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  let table: string[] = [];

  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`}>
          {list.map((li, i) => (
            <li key={i}>{inline(li, `li-${blocks.length}-${i}`)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  const flushTable = () => {
    if (table.length) {
      const rows = table
        .filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)) // drop separator row
        .map((r) => r.split("|").map((c) => c.trim()).filter((c, i, a) => !(c === "" && (i === 0 || i === a.length - 1))));
      const [head, ...body] = rows;
      blocks.push(
        <div key={`tbl-${blocks.length}`} className="my-2 overflow-x-auto">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr>
                {head?.map((c, i) => (
                  <th key={i} className="border-b border-border px-2 py-1 text-left font-medium text-ink-3">
                    {inline(c, `th-${i}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {body.map((r, ri) => (
                <tr key={ri}>
                  {r.map((c, ci) => (
                    <td key={ci} className="border-b border-border/60 px-2 py-1 text-ink-2">
                      {inline(c, `td-${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      table = [];
    }
  };

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    if (line.startsWith("|")) {
      flushList();
      table.push(line);
      return;
    }
    flushTable();
    if (line.startsWith("- ")) {
      list.push(line.slice(2));
      return;
    }
    flushList();
    if (line.startsWith("### ")) {
      blocks.push(<h3 key={idx}>{inline(line.slice(4), `h3-${idx}`)}</h3>);
    } else if (line.startsWith("## ")) {
      blocks.push(<h2 key={idx}>{inline(line.slice(3), `h2-${idx}`)}</h2>);
    } else if (line.startsWith("# ")) {
      blocks.push(<h1 key={idx}>{inline(line.slice(2), `h1-${idx}`)}</h1>);
    } else if (line === "---") {
      blocks.push(<hr key={idx} />);
    } else if (line.trim() === "") {
      // paragraph break — skip
    } else {
      blocks.push(<p key={idx}>{inline(line, `p-${idx}`)}</p>);
    }
  });
  flushList();
  flushTable();

  return <div className={`resume-prose ${className}`}>{blocks}</div>;
}
