import type { JSX, ReactNode } from "react";

// Just enough Markdown for the policy file: headings, rules, bullets, tables and inline emphasis.

export function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <em key={i}>{part.slice(1, -1)}</em>;
    return part;
  });
}

function cells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

export function PolicyText({ text }: { text: string }) {
  const out: JSX.Element[] = [];
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const key = out.length;
    if (!line.trim()) {
      i++;
    } else if (line.trim() === "---") {
      out.push(<hr key={key} />);
      i++;
    } else if (/^#{1,3} /.test(line)) {
      const depth = line.indexOf(" ");
      const body = inline(line.slice(depth + 1));
      out.push(depth === 1 ? <h3 key={key}>{body}</h3> : depth === 2 ? <h4 key={key}>{body}</h4> : <h5 key={key}>{body}</h5>);
      i++;
    } else if (line.startsWith("|")) {
      const rows: string[][] = [];
      for (; i < lines.length && lines[i]!.startsWith("|"); i++) {
        if (!/^\|[\s|:-]+\|$/.test(lines[i]!.trim())) rows.push(cells(lines[i]!));
      }
      const [head = [], ...body] = rows;
      out.push(
        <table key={key}>
          <thead>
            <tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr>
          </thead>
          <tbody>
            {body.map((r, k) => (
              <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      );
    } else if (line.startsWith("- ")) {
      const bullets: string[] = [];
      for (; i < lines.length && (lines[i]!.startsWith("- ") || lines[i]!.startsWith("  ")); i++) {
        if (lines[i]!.startsWith("- ")) bullets.push(lines[i]!.slice(2));
        else bullets[bullets.length - 1] += ` ${lines[i]!.trim()}`;
      }
      out.push(
        <ul key={key}>
          {bullets.map((b, j) => <li key={j}>{inline(b)}</li>)}
        </ul>,
      );
    } else {
      const para: string[] = [];
      for (; i < lines.length && lines[i]!.trim() && !/^(#|\||- |---)/.test(lines[i]!); i++) para.push(lines[i]!);
      out.push(<p key={key}>{inline(para.join(" "))}</p>);
    }
  }
  return <div className="policy-text">{out}</div>;
}
