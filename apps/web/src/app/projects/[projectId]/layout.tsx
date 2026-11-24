"use client";

import { MoreHorizontalIcon, PencilIcon, PlayIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { DropdownMenu } from "radix-ui";
import { useState } from "react";

import { DeleteProjectDialog, EditProjectDialog, NewRunDialog } from "@/components/project/project-dialogs";
import { DepthBadge, RunStatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { ApiError } from "@/lib/api";
import { isActive, useProject } from "@/lib/queries";
import { cn } from "@/lib/utils";

const TABS = [
  { segment: "", label: "Overview" },
  { segment: "report", label: "Report" },
  { segment: "sources", label: "Sources" },
  { segment: "evidence", label: "Evidence" },
  { segment: "chat", label: "Chat" },
];

export default function ProjectLayout({ children }: { children: React.ReactNode }) {
  const { projectId } = useParams<{ projectId: string }>();
  const pathname = usePathname();
  const project = useProject(projectId);
  const [dialog, setDialog] = useState<"run" | "edit" | "delete" | null>(null);
  const base = `/projects/${projectId}`;
  const current = pathname.slice(base.length).split("/")[1] ?? "";
  const activeTab = current === "runs" ? "" : current;

  if (project.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-4 w-full max-w-xl" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }
  if (project.isError) {
    if (project.error instanceof ApiError && project.error.status === 404) {
      return (
        <div className="space-y-3 py-16 text-center">
          <h1 className="text-xl font-semibold">Project not found</h1>
          <p className="text-sm text-muted-foreground">It may have been deleted.</p>
          <Button asChild variant="outline">
            <Link href="/">Back to projects</Link>
          </Button>
        </div>
      );
    }
    return <ErrorState error={project.error} retry={() => void project.refetch()} />;
  }

  const data = project.data;
  const running = isActive(data.latest_run?.status);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{data.title}</h1>
            <DepthBadge depth={data.depth} />
            {data.latest_run && <RunStatusBadge status={data.latest_run.status} />}
          </div>
          <p className="line-clamp-2 max-w-3xl whitespace-pre-line text-sm text-muted-foreground" title={data.objective}>
            {data.objective}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            onClick={() => setDialog("run")}
            disabled={running}
            title={running ? "A run is already in progress" : undefined}
          >
            <PlayIcon /> New run
          </Button>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <Button variant="outline" size="icon" aria-label="More actions">
                <MoreHorizontalIcon />
              </Button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="end"
                sideOffset={6}
                className="z-50 min-w-40 rounded-md border bg-popover p-1 text-sm shadow-md"
              >
                <DropdownMenu.Item
                  className="flex cursor-default items-center gap-2 rounded px-2 py-1.5 outline-none data-[highlighted]:bg-accent"
                  onSelect={() => setDialog("edit")}
                >
                  <PencilIcon className="size-4" /> Edit project
                </DropdownMenu.Item>
                <DropdownMenu.Item
                  className="flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-destructive outline-none data-[highlighted]:bg-destructive/10"
                  onSelect={() => setDialog("delete")}
                >
                  <Trash2Icon className="size-4" /> Delete project
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </div>

      <nav aria-label="Project" className="-mx-4 overflow-x-auto border-b px-4 sm:mx-0 sm:px-0">
        <ul className="flex gap-1">
          {TABS.map((tab) => (
            <li key={tab.segment}>
              <Link
                href={tab.segment ? `${base}/${tab.segment}` : base}
                aria-current={activeTab === tab.segment ? "page" : undefined}
                className={cn(
                  "-mb-px inline-block border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground",
                  activeTab === tab.segment && "border-primary font-medium text-foreground",
                )}
              >
                {tab.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      {children}

      {dialog === "run" && <NewRunDialog project={data} open onOpenChange={(o) => !o && setDialog(null)} />}
      {dialog === "edit" && (
        <EditProjectDialog project={data} open onOpenChange={(o) => !o && setDialog(null)} />
      )}
      {dialog === "delete" && (
        <DeleteProjectDialog project={data} open onOpenChange={(o) => !o && setDialog(null)} />
      )}
    </div>
  );
}
