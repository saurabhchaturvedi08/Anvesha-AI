"use client";

import { ExternalLinkIcon } from "lucide-react";
import { useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CitationChip, type CitationInfo } from "@/components/citation-chip";
import { remarkCitations } from "@/lib/citations";
import { cn } from "@/lib/utils";

/**
 * Renders report and chat Markdown. The text comes from models reading untrusted web
 * pages, so raw HTML is never rendered, links open in a new tab without a referrer, and
 * images are shown as links (loading them would let a page track readers).
 */
export function Markdown({
  markdown,
  citations = [],
  compact = false,
  className,
}: {
  markdown: string;
  citations?: CitationInfo[];
  compact?: boolean;
  className?: string;
}) {
  const byNumber = useMemo(() => new Map(citations.map((c) => [c.number, c])), [citations]);
  const known = useMemo(() => new Set(byNumber.keys()), [byNumber]);
  const remarkPlugins = useMemo(
    () => [remarkGfm, [remarkCitations, { known }] as [typeof remarkCitations, { known: Set<number> }]],
    [known],
  );
  const components = useMemo<Components>(
    () => ({
      a: ({ href, children }) => (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow">
          {children}
        </a>
      ),
      img: ({ src, alt }) =>
        typeof src === "string" && src ? (
          <a href={src} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1">
            {alt || "Image"} <ExternalLinkIcon className="inline size-3" aria-hidden />
          </a>
        ) : null,
      sup: ({ node, children, ...props }) => {
        const value = node?.properties?.dataCitations;
        if (typeof value === "string" && value) {
          const numbers = value.split(",").map(Number);
          return <CitationChip numbers={numbers} citations={byNumber} />;
        }
        return <sup {...props}>{children}</sup>;
      },
    }),
    [byNumber],
  );
  return (
    <div className={cn("prose-report", compact && "prose-compact", className)}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
