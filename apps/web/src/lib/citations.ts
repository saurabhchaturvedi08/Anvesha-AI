import type { PhrasingContent, Root } from "mdast";
import { visit } from "unist-util-visit";

/** Citation markers the backend renders into reports and chat replies: [1] or [2, 5]. */
const MARKER = /\[(\d+(?:\s*,\s*\d+)*)\]/g;

export type TextPart = { type: "text"; value: string } | { type: "cite"; numbers: number[] };

/** Split text into plain parts and citation markers. Only known numbers count as citations. */
export function splitCitations(text: string, known: ReadonlySet<number>): TextPart[] {
  const parts: TextPart[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const numbers = (match[1] ?? "").split(",").map((n) => Number(n.trim()));
    if (!numbers.every((n) => known.has(n))) continue;
    if (match.index > last) parts.push({ type: "text", value: text.slice(last, match.index) });
    parts.push({ type: "cite", numbers });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ type: "text", value: text.slice(last) });
  return parts;
}

/**
 * A remark plugin that turns citation markers into `<sup data-citations="1,2">` elements,
 * which the Markdown renderer swaps for interactive citation chips.
 */
export function remarkCitations(options: { known: ReadonlySet<number> }) {
  return (tree: Root) => {
    visit(tree, "text", (node, index, parent) => {
      if (!parent || index === undefined || parent.type === "link" || parent.type === "linkReference") {
        return;
      }
      const parts = splitCitations(node.value, options.known);
      if (!parts.some((part) => part.type === "cite")) return;
      const replacement = parts.map((part): PhrasingContent => {
        if (part.type === "text") return { type: "text", value: part.value };
        return {
          type: "citation",
          data: {
            hName: "sup",
            hProperties: { dataCitations: part.numbers.join(",") },
          },
          children: [{ type: "text", value: `[${part.numbers.join(", ")}]` }],
        } as unknown as PhrasingContent;
      });
      parent.children.splice(index, 1, ...replacement);
      return index + replacement.length;
    });
  };
}
