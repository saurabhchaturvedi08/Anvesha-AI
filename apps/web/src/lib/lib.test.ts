import type { RunEvent } from "@anvesha/api-client";
import type { Root } from "mdast";
import { describe, expect, it } from "vitest";

import { errorMessage } from "@/lib/api";
import { remarkCitations, splitCitations } from "@/lib/citations";
import { formatUsd, hostname, titleFromQuestion, toNumber, withClarifications } from "@/lib/format";
import { agentLabel, describeEvent } from "@/lib/run-events";
import { parseSse } from "@/lib/sse";

describe("citations", () => {
  const known = new Set([1, 2, 3]);

  it("splits known markers out of text", () => {
    expect(splitCitations("Solar grew [1] and wind [2, 3].", known)).toEqual([
      { type: "text", value: "Solar grew " },
      { type: "cite", numbers: [1] },
      { type: "text", value: " and wind " },
      { type: "cite", numbers: [2, 3] },
      { type: "text", value: "." },
    ]);
  });

  it("leaves unknown numbers alone (years, list labels)", () => {
    expect(splitCitations("In [2025] and [4]", known)).toEqual([{ type: "text", value: "In [2025] and [4]" }]);
    expect(splitCitations("Mixed [1, 9]", known)).toEqual([{ type: "text", value: "Mixed [1, 9]" }]);
  });

  it("replaces markers in Markdown text but not inside links", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            { type: "text", value: "Claim [1]." },
            { type: "link", url: "https://x", children: [{ type: "text", value: "see [2]" }] },
          ],
        },
      ],
    };
    remarkCitations({ known })(tree);
    const paragraph = tree.children[0] as { children: { type: string; data?: unknown }[] };
    expect(paragraph.children.map((node) => node.type)).toEqual(["text", "citation", "text", "link"]);
    expect(paragraph.children[1]!.data).toEqual({
      hName: "sup",
      hProperties: { dataCitations: "1" },
    });
  });
});

describe("parseSse", () => {
  it("parses complete events and keeps the partial remainder", () => {
    const { events, rest } = parseSse(
      'retry: 3000\n\nid: 5\nevent: tool_call\ndata: {"a":1}\n\n: keepalive\n\nevent: end\ndata: {}\n\nid: 6\nda',
    );
    expect(events).toEqual([
      { id: "5", event: "tool_call", data: '{"a":1}' },
      { id: null, event: "end", data: "{}" },
    ]);
    expect(rest).toBe("id: 6\nda");
  });

  it("handles CRLF and multi-line data", () => {
    expect(parseSse("data: a\r\ndata: b\r\n\r\n").events).toEqual([{ id: null, event: "message", data: "a\nb" }]);
  });
});

describe("run events", () => {
  const event = (type: string, data: Record<string, unknown>, agent: string | null = null): RunEvent => ({
    id: 1,
    type,
    agent,
    data,
    created_at: "2026-11-24T10:00:00Z",
  });

  it("describes agent work in plain language", () => {
    expect(
      describeEvent(event("tool_call", { tool: "fetch_page", arguments: { url: "https://www.iea.org/x" } }))?.title,
    ).toBe("Reading iea.org");
    expect(describeEvent(event("finding_rejected", { finding_id: "f", reason: "Overstates" }))?.tone).toBe("warning");
    expect(describeEvent(event("run_completed", { report_status: "incomplete" }))?.title).toBe(
      "Finished with a partial report",
    );
  });

  it("hides successful tool results", () => {
    expect(describeEvent(event("tool_result", { tool: "web_search", ok: true }))).toBeNull();
    expect(describeEvent(event("tool_result", { tool: "fetch_page", ok: false }))).not.toBeNull();
  });

  it("labels agents", () => {
    expect(agentLabel("researcher:2.1")).toBe("Researcher 2.1");
    expect(agentLabel("lead")).toBe("Lead researcher");
    expect(agentLabel(null)).toBe("Anvesha");
  });
});

describe("formatting", () => {
  it("formats money from API decimals", () => {
    expect(toNumber("2.50")).toBe(2.5);
    expect(formatUsd("2.5")).toBe("$2.50");
    expect(formatUsd("0.004")).toBe("<$0.01");
    expect(formatUsd(null)).toBe("—");
  });

  it("makes titles and hostnames", () => {
    expect(titleFromQuestion("What is solar power?\nMore detail")).toBe("What is solar power");
    expect(titleFromQuestion("word ".repeat(40)).endsWith("…")).toBe(true);
    expect(hostname("https://www.example.org/a")).toBe("example.org");
    expect(hostname("not a url")).toBeNull();
  });

  it("appends clarifications", () => {
    expect(withClarifications(" Q? ", [["Region?", "Europe"], ["Years?", " "]])).toBe(
      "Q?\n\nClarifications:\n- Region? Europe",
    );
    expect(withClarifications("Q?", [])).toBe("Q?");
  });

  it("explains API errors", () => {
    expect(errorMessage({ detail: "Nope", code: "x" }, "fallback")).toBe("Nope");
    expect(errorMessage({ detail: [{ loc: ["body", "question"], msg: "too short" }] }, "f")).toBe(
      "question: too short",
    );
    expect(errorMessage(null, "fallback")).toBe("fallback");
  });
});
