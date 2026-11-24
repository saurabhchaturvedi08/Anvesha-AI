"use client";

import type { RunEvent } from "@anvesha/api-client";
import {
  BookOpenIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  CircleSlashIcon,
  ClipboardListIcon,
  FileSearchIcon,
  GlobeIcon,
  GraduationCapIcon,
  HandIcon,
  type LucideIcon,
  MessageSquareTextIcon,
  OctagonXIcon,
  PenLineIcon,
  PlayIcon,
  QuoteIcon,
  RefreshCwIcon,
  SearchIcon,
  ShieldCheckIcon,
  ThumbsDownIcon,
  TriangleAlertIcon,
  UserCheckIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Spinner } from "@/components/ui/feedback";
import { formatDateTime } from "@/lib/format";
import { describeEvent, type EventIcon, type EventView, type Tone } from "@/lib/run-events";
import type { StreamState } from "@/lib/use-run-events";
import { cn } from "@/lib/utils";

const ICONS: Record<EventIcon, LucideIcon> = {
  queue: CircleDashedIcon,
  play: PlayIcon,
  plan: ClipboardListIcon,
  wait: HandIcon,
  check: UserCheckIcon,
  search: SearchIcon,
  paper: GraduationCapIcon,
  page: GlobeIcon,
  read: BookOpenIcon,
  note: MessageSquareTextIcon,
  finding: QuoteIcon,
  reject: ThumbsDownIcon,
  stop: OctagonXIcon,
  verify: ShieldCheckIcon,
  review: FileSearchIcon,
  write: PenLineIcon,
  retry: RefreshCwIcon,
  error: TriangleAlertIcon,
  done: CheckCircle2Icon,
  cancel: CircleSlashIcon,
};

const TONES: Record<Tone, string> = {
  default: "bg-muted text-foreground",
  muted: "bg-muted/60 text-muted-foreground",
  primary: "bg-primary/12 text-primary",
  success: "bg-success/15 text-success",
  warning: "bg-warning/20 text-amber-700 dark:text-warning",
  danger: "bg-destructive/12 text-destructive",
};

const STATE_LABEL: Record<StreamState, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  ended: "Finished",
  error: "Unavailable",
};

export function ActivityFeed({ events, state }: { events: RunEvent[]; state: StreamState }) {
  const views = useMemo(
    () => events.map(describeEvent).filter((view): view is EventView => view !== null),
    [events],
  );
  const agents = useMemo(() => Array.from(new Set(views.map((v) => v.agent))), [views]);
  const [agent, setAgent] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(true);
  const shown = agent ? views.filter((v) => v.agent === agent) : views;

  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const element = scroller.current;
    if (element && stick.current) element.scrollTop = element.scrollHeight;
  }, [shown.length]);

  return (
    <section aria-labelledby="activity-heading" className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <h2 id="activity-heading" className="font-semibold">
          Activity
        </h2>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 text-xs",
            state === "live" ? "text-success" : "text-muted-foreground",
          )}
        >
          {state === "live" ? (
            <span className="size-2 animate-pulse rounded-full bg-success" aria-hidden />
          ) : state === "connecting" || state === "reconnecting" ? (
            <Spinner className="size-3" />
          ) : null}
          {STATE_LABEL[state]}
        </span>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={showDetails}
            onChange={(event) => setShowDetails(event.target.checked)}
          />
          Details
        </label>
      </div>
      {agents.length > 1 && (
        <div className="flex gap-1 overflow-x-auto border-b px-4 py-2" role="group" aria-label="Filter by agent">
          {[null, ...agents].map((name) => (
            <button
              key={name ?? "all"}
              type="button"
              aria-pressed={agent === name}
              onClick={() => setAgent(name)}
              className={cn(
                "whitespace-nowrap rounded-full px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted",
                agent === name && "bg-secondary font-medium text-secondary-foreground",
              )}
            >
              {name ?? "All agents"}
            </button>
          ))}
        </div>
      )}
      <div
        ref={scroller}
        onScroll={(event) => {
          const element = event.currentTarget;
          stick.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
        }}
        className="max-h-[65vh] min-h-48 overflow-y-auto px-4 py-3"
        aria-live="polite"
        aria-relevant="additions"
      >
        {shown.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            {state === "connecting" ? "Loading activity…" : "Nothing yet. Activity appears here as the agents work."}
          </p>
        ) : (
          <ol className="space-y-3">
            {shown.map((view) => {
              const Icon = ICONS[view.icon];
              return (
                <li key={view.id} className="flex gap-3">
                  <span className={cn("mt-0.5 grid size-7 shrink-0 place-items-center rounded-full", TONES[view.tone])}>
                    <Icon className="size-3.5" aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">
                      <span className="font-medium">{view.agent}</span>{" "}
                      <span className={view.tone === "muted" ? "text-muted-foreground" : undefined}>
                        {view.title}
                      </span>
                    </p>
                    {showDetails && view.detail && (
                      <p className="mt-0.5 line-clamp-4 whitespace-pre-line break-words text-xs text-muted-foreground">
                        {view.detail}
                      </p>
                    )}
                  </div>
                  <time
                    dateTime={view.at}
                    title={formatDateTime(view.at)}
                    className="shrink-0 pt-0.5 text-xs tabular-nums text-muted-foreground"
                  >
                    {new Date(view.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </time>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </section>
  );
}
