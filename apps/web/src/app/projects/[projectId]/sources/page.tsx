"use client";

import type { Source } from "@anvesha/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BotIcon, FileIcon, FileUpIcon, GlobeIcon, LibraryIcon, PlusIcon, Trash2Icon, UserIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState, ErrorState, Skeleton, Spinner } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { api, describeError, unwrap, uploadSource } from "@/lib/api";
import { formatCompact, hostname, timeAgo } from "@/lib/format";
import { keys, useServerConfig, useSources } from "@/lib/queries";

export default function SourcesPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const queryClient = useQueryClient();
  const sources = useSources(projectId);
  const config = useServerConfig();
  const [url, setUrl] = useState("");
  const [filter, setFilter] = useState("");
  const [uploading, setUploading] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Source | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.sources(projectId) });

  const add = useMutation({
    mutationFn: (value: string) =>
      unwrap(
        api.POST("/api/v1/projects/{project_id}/sources", {
          params: { path: { project_id: projectId } },
          body: { url: value },
        }),
      ),
    onSuccess: (source) => {
      toast.success(source.created ? "Source saved" : "Already saved", {
        description: source.title ?? source.url ?? undefined,
      });
      setUrl("");
      void refresh();
    },
    onError: (error) => toast.error("Couldn't save that page", { description: describeError(error) }),
  });

  const remove = useMutation({
    mutationFn: (source: Source) =>
      unwrap(
        api.DELETE("/api/v1/projects/{project_id}/sources/{source_id}", {
          params: { path: { project_id: projectId, source_id: source.id } },
        }),
      ),
    onSuccess: () => {
      toast.success("Source deleted");
      setToDelete(null);
      void refresh();
    },
    onError: (error) => toast.error(describeError(error)),
  });

  const upload = async (files: File[]) => {
    const limit = config.data?.upload_max_bytes;
    for (const [index, file] of files.entries()) {
      if (limit && file.size > limit) {
        toast.error(`${file.name} is too large`, {
          description: `The limit is ${Math.round(limit / (1024 * 1024))} MB.`,
        });
        continue;
      }
      setUploading(files.length > 1 ? `Uploading ${index + 1} of ${files.length}…` : "Uploading…");
      try {
        await uploadSource(projectId, file);
        toast.success(`Added ${file.name}`);
      } catch (error) {
        toast.error(`Couldn't add ${file.name}`, { description: describeError(error) });
      }
    }
    setUploading(null);
    void refresh();
  };

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return sources.data ?? [];
    return (sources.data ?? []).filter((s) =>
      [s.title, s.url, s.filename, s.site_name].some((v) => v?.toLowerCase().includes(needle)),
    );
  }, [sources.data, filter]);

  return (
    <div className="space-y-5">
      <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
        <form
          className="flex flex-1 gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (url.trim()) add.mutate(url.trim());
          }}
        >
          <Input
            type="url"
            required
            aria-label="Page or PDF link"
            placeholder="Add a page or PDF by link: https://…"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
          <Button type="submit" disabled={add.isPending || !url.trim()}>
            {add.isPending ? <Spinner /> : <PlusIcon />} Add
          </Button>
        </form>
        <input
          ref={fileInput}
          type="file"
          multiple
          className="sr-only"
          aria-label="Upload files"
          accept=".pdf,.txt,.md,.markdown,.html,.htm,application/pdf,text/plain,text/markdown,text/html"
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            void upload(files);
          }}
        />
        <Button variant="outline" onClick={() => fileInput.current?.click()} disabled={!!uploading}>
          {uploading ? <Spinner /> : <FileUpIcon />} {uploading ?? "Upload files"}
        </Button>
      </Card>

      {sources.isPending ? (
        <Skeleton className="h-64" />
      ) : sources.isError ? (
        <ErrorState error={sources.error} retry={() => void sources.refetch()} />
      ) : sources.data.length === 0 ? (
        <EmptyState icon={LibraryIcon} title="No sources yet">
          Sources are saved here as agents read pages and papers. You can add your own too.
        </EmptyState>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {sources.data.length} source{sources.data.length === 1 ? "" : "s"}
            </p>
            <Input
              className="max-w-xs"
              aria-label="Filter sources"
              placeholder="Filter by title or site"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
          <ul className="divide-y rounded-lg border bg-card">
            {shown.map((source) => (
              <li key={source.id} className="flex items-center gap-3 px-4 py-3">
                {source.origin === "upload" ? (
                  <FileIcon className="size-4 shrink-0 text-muted-foreground" aria-label="Uploaded file" />
                ) : (
                  <GlobeIcon className="size-4 shrink-0 text-muted-foreground" aria-label="Web page" />
                )}
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/projects/${projectId}/sources/${source.id}`}
                    className="line-clamp-1 text-sm font-medium hover:underline"
                  >
                    {source.title || source.filename || source.url || "Untitled"}
                  </Link>
                  <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                    <span>{hostname(source.url) ?? source.filename ?? source.source_type.toUpperCase()}</span>
                    <span>·</span>
                    <span>{formatCompact(source.char_count)} characters</span>
                    {source.published && (
                      <>
                        <span>·</span>
                        <span>{source.published}</span>
                      </>
                    )}
                    <span>·</span>
                    <span>{timeAgo(source.created_at)}</span>
                  </p>
                </div>
                <div className="hidden items-center gap-2 sm:flex">
                  {source.source_type === "pdf" && <Badge tone="outline">PDF</Badge>}
                  {source.embedding_status === "failed" && (
                    <Badge tone="warning" title={source.embedding_error ?? undefined}>
                      Keyword search only
                    </Badge>
                  )}
                  <Badge tone="muted">
                    {source.added_by === "agent" ? <BotIcon /> : <UserIcon />}
                    {source.added_by === "agent" ? "Agent" : "You"}
                  </Badge>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete ${source.title ?? "source"}`}
                  onClick={() => setToDelete(source)}
                >
                  <Trash2Icon />
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Dialog open={!!toDelete} onOpenChange={(open) => !open && setToDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this source?</DialogTitle>
            <DialogDescription>
              “{toDelete?.title || toDelete?.url || toDelete?.filename}” will be removed, along with
              any findings that quote it. Existing reports keep their text.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setToDelete(null)}>
              Keep it
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => toDelete && remove.mutate(toDelete)}
            >
              {remove.isPending && <Spinner />} Delete source
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
