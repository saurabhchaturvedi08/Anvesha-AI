"use client";

import { ArrowRightIcon, BookOpenCheckIcon, PlusIcon } from "lucide-react";
import Link from "next/link";

import { SetupBanner } from "@/components/setup-banner";
import { DepthBadge, RunStatusBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { timeAgo } from "@/lib/format";
import { useProjects } from "@/lib/queries";

export default function DashboardPage() {
  const projects = useProjects();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Research projects</h1>
          <p className="text-sm text-muted-foreground">
            Every claim in a report is backed by a quote checked word for word against its source.
          </p>
        </div>
        <Button asChild>
          <Link href="/new">
            <PlusIcon /> New research
          </Link>
        </Button>
      </div>

      <SetupBanner />

      {projects.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : projects.isError ? (
        <ErrorState error={projects.error} retry={() => void projects.refetch()} />
      ) : projects.data.length === 0 ? (
        <EmptyState
          icon={BookOpenCheckIcon}
          title="No research yet"
          action={
            <Button asChild>
              <Link href="/new">Start your first research</Link>
            </Button>
          }
        >
          Ask a question. Agents plan the work, read web pages and papers, check every quote, and
          write a cited report.
        </EmptyState>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data.map((project) => (
            <li key={project.id}>
              <Link href={`/projects/${project.id}`} className="group block h-full">
                <Card className="flex h-full flex-col gap-3 p-5 transition-colors group-hover:border-primary/40">
                  <div className="flex items-start justify-between gap-3">
                    <h2 className="line-clamp-2 font-medium leading-snug">{project.title}</h2>
                    <ArrowRightIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                  </div>
                  <p className="line-clamp-3 flex-1 text-sm text-muted-foreground">{project.objective}</p>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    {project.latest_run ? (
                      <RunStatusBadge status={project.latest_run.status} />
                    ) : (
                      <span>Not started</span>
                    )}
                    <DepthBadge depth={project.depth} />
                    <span className="ml-auto">{timeAgo(project.latest_run?.created_at ?? project.updated_at)}</span>
                  </div>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
