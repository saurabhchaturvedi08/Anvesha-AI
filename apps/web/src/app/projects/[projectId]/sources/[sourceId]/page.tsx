"use client";

import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, ExternalLinkIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout, ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { api, describeError, unwrap } from "@/lib/api";
import { formatBytes, formatDateTime, formatNumber, hostname } from "@/lib/format";
import { keys, useSource } from "@/lib/queries";

const PAGE_CHARS = 20_000;
const CONTEXT_BEFORE = 1_500;

export default function SourcePage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <SourceView />
    </Suspense>
  );
}

function SourceView() {
  const { projectId, sourceId } = useParams<{ projectId: string; sourceId: string }>();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const source = useSource(projectId, sourceId);
  const start = Number(searchParams.get("start") ?? NaN);
  const end = Number(searchParams.get("end") ?? NaN);
  const highlight = Number.isFinite(start) && Number.isFinite(end) && end > start ? { start, end } : null;
  const firstOffset = highlight ? Math.max(0, highlight.start - CONTEXT_BEFORE) : 0;

  const text = useInfiniteQuery({
    queryKey: [...keys.source(projectId, sourceId), "text", firstOffset],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/projects/{project_id}/sources/{source_id}/text", {
          params: {
            path: { project_id: projectId, source_id: sourceId },
            query: { offset: pageParam, limit: PAGE_CHARS },
          },
        }),
      ),
    initialPageParam: firstOffset,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    staleTime: Infinity,
  });

  const reindex = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/sources/{source_id}/embed", {
          params: { path: { project_id: projectId, source_id: sourceId } },
        }),
      ),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: keys.source(projectId, sourceId) });
      if (result.embedding_status === "ready") toast.success("Semantic search is ready for this source");
      else toast.warning("Indexing didn't finish", { description: result.embedding_error ?? undefined });
    },
    onError: (error) => toast.error(describeError(error)),
  });

  const mark = useRef<HTMLElement>(null);
  const loaded = !!text.data;
  useEffect(() => {
    if (loaded) mark.current?.scrollIntoView({ block: "center" });
  }, [loaded]);

  if (source.isPending) return <Skeleton className="h-96" />;
  if (source.isError) return <ErrorState error={source.error} retry={() => void source.refetch()} />;
  const data = source.data;
  const pages = text.data?.pages ?? [];
  const loadedFrom = pages[0]?.offset ?? firstOffset;

  return (
    <div className="space-y-4">
      <Link
        href={`/projects/${projectId}/sources`}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeftIcon className="size-3" /> All sources
      </Link>
      <Card className="space-y-3 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <h2 className="text-lg font-semibold leading-snug">{data.title || data.filename || "Untitled source"}</h2>
            <p className="text-sm text-muted-foreground">
              {[data.authors.slice(0, 4).join(", "), data.site_name, data.published].filter(Boolean).join(" · ")}
            </p>
          </div>
          {data.url && (
            <Button asChild variant="outline" size="sm">
              <a href={data.url} target="_blank" rel="noopener noreferrer nofollow">
                {hostname(data.url)} <ExternalLinkIcon />
              </a>
            </Button>
          )}
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge tone="outline">{data.source_type.toUpperCase()}</Badge>
          <Badge tone="muted">{formatNumber(data.char_count)} characters</Badge>
          {data.byte_size > 0 && <Badge tone="muted">{formatBytes(data.byte_size)}</Badge>}
          {data.doi && <Badge tone="muted">DOI {data.doi}</Badge>}
          <Badge tone="muted">{data.added_by === "agent" ? "Saved by an agent" : "Added by you"}</Badge>
          {data.fetched_at && <Badge tone="muted">Fetched {formatDateTime(data.fetched_at)}</Badge>}
        </div>
        {(data.embedding_status === "failed" || data.embedding_status === "pending") && (
          <Callout
            tone="warning"
            title="Semantic search isn't available for this source"
            action={
              <Button size="sm" variant="outline" onClick={() => reindex.mutate()} disabled={reindex.isPending}>
                {reindex.isPending ? <Spinner /> : <RefreshCwIcon />} Index again
              </Button>
            }
          >
            {data.embedding_error ?? "Indexing hasn't run yet."} Keyword search still works.
          </Callout>
        )}
      </Card>

      <Card className="p-5 sm:p-8">
        {text.isPending ? (
          <Skeleton className="h-64" />
        ) : text.isError ? (
          <ErrorState error={text.error} retry={() => void text.refetch()} />
        ) : (
          <div className="space-y-4">
            {loadedFrom > 0 && (
              <p className="text-xs text-muted-foreground">
                Showing from character {formatNumber(loadedFrom)}.{" "}
                <Link href={`/projects/${projectId}/sources/${sourceId}`} className="underline">
                  Read from the start
                </Link>
              </p>
            )}
            <div className="whitespace-pre-wrap break-words font-serif text-[0.95rem] leading-relaxed">
              {pages.map((page) => (
                <TextChunk key={page.offset} offset={page.offset} text={page.text} highlight={highlight} markRef={mark} />
              ))}
            </div>
            {text.hasNextPage && (
              <Button variant="outline" onClick={() => void text.fetchNextPage()} disabled={text.isFetchingNextPage}>
                {text.isFetchingNextPage && <Spinner />} Load more
              </Button>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

function TextChunk({
  offset,
  text,
  highlight,
  markRef,
}: {
  offset: number;
  text: string;
  highlight: { start: number; end: number } | null;
  markRef: React.RefObject<HTMLElement | null>;
}) {
  if (!highlight || highlight.end <= offset || highlight.start >= offset + text.length) {
    return <>{text}</>;
  }
  const from = Math.max(0, highlight.start - offset);
  const to = Math.min(text.length, highlight.end - offset);
  return (
    <>
      {text.slice(0, from)}
      <mark ref={markRef} className="rounded-sm bg-highlight px-0.5 text-foreground">
        {text.slice(from, to)}
      </mark>
      {text.slice(to)}
    </>
  );
}
