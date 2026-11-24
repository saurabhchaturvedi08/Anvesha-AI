"use client";

import { ExternalLinkIcon, FileTextIcon, QuoteIcon } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { hostname } from "@/lib/format";

export type CitationQuote = {
  quote: string;
  statement?: string | null;
  page?: number | null;
};

export type CitationInfo = {
  number: number;
  title: string | null;
  url: string | null;
  /** In-app link to the saved source, if known. */
  sourceHref?: string;
  quotes: CitationQuote[];
};

const MAX_QUOTES = 3;
const HOVER_DELAY_MS = 120;

/** A citation marker like [1, 2]. Hover (or tap, or focus and press Enter) to see the evidence. */
export function CitationChip({
  numbers,
  citations,
}: {
  numbers: number[];
  citations: Map<number, CitationInfo>;
}) {
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const items = numbers.map((n) => citations.get(n)).filter((c): c is CitationInfo => !!c);

  // Hover opens it for mouse users; touch and keyboard users tap or press Enter instead.
  const onPointerEnter = (event: React.PointerEvent) => {
    if (event.pointerType !== "mouse") return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(true), HOVER_DELAY_MS);
  };
  const onPointerLeave = (event: React.PointerEvent) => {
    if (event.pointerType !== "mouse") return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(false), HOVER_DELAY_MS * 2);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          onPointerEnter={onPointerEnter}
          onPointerLeave={onPointerLeave}
          className="mx-0.5 inline-flex -translate-y-[0.35em] items-center rounded px-1 align-baseline font-sans text-[0.7em] font-semibold leading-tight text-primary transition-colors hover:bg-primary/10 data-[state=open]:bg-primary/15"
          aria-label={`Sources ${numbers.join(", ")}`}
        >
          [{numbers.join(", ")}]
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[26rem] space-y-4 p-0"
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        <div className="max-h-[60vh] divide-y overflow-y-auto">
          {items.map((citation) => (
            <CitationCard key={citation.number} citation={citation} />
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function CitationCard({ citation }: { citation: CitationInfo }) {
  const host = hostname(citation.url);
  return (
    <div className="space-y-3 p-4 font-sans">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded bg-primary/12 text-xs font-semibold text-primary">
          {citation.number}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium leading-snug">{citation.title || host || "Untitled source"}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {citation.url && (
              <a
                href={citation.url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="inline-flex items-center gap-1 hover:text-foreground"
              >
                {host ?? "Open"} <ExternalLinkIcon className="size-3" aria-hidden />
              </a>
            )}
            {citation.sourceHref && (
              <Link href={citation.sourceHref} className="inline-flex items-center gap-1 hover:text-foreground">
                <FileTextIcon className="size-3" aria-hidden /> Saved text
              </Link>
            )}
          </div>
        </div>
      </div>
      {citation.quotes.slice(0, MAX_QUOTES).map((quote, index) => (
        <figure key={index} className="space-y-1.5">
          {quote.statement && <figcaption className="text-xs font-medium">{quote.statement}</figcaption>}
          <blockquote className="flex gap-2 rounded-md bg-muted/70 p-2.5 text-[0.8125rem] leading-relaxed">
            <QuoteIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" aria-hidden />
            <span>
              {quote.quote}
              {quote.page ? <span className="text-muted-foreground"> (p. {quote.page})</span> : null}
            </span>
          </blockquote>
        </figure>
      ))}
      {citation.quotes.length > MAX_QUOTES && (
        <p className="text-xs text-muted-foreground">
          +{citation.quotes.length - MAX_QUOTES} more quote(s) from this source
        </p>
      )}
    </div>
  );
}
